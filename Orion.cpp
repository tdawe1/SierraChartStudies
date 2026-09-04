#include "sierrachart.h"

#include <cstdlib>
#include <cmath>

namespace orion {

constexpr int kMaxLevels = 1024;

struct VapLevel {
	int price_ticks = 0;
	double bid = 0;
	double ask = 0;

	double volume() const { return bid + ask; }
	double delta() const { return ask - bid; }
};

struct GroupedLevel {
	int bucket = 0;
	double bid = 0;
	double ask = 0;

	double volume() const { return bid + ask; }
	double delta() const { return ask - bid; }
};

struct StackResult {
	int stacked = 0;
	int scale = 1;
	int poc_bucket = 0;
	int poc_ticks = 0;
	int zone_low_bucket = 0;
	int zone_high_bucket = 0;
	int zone_low_ticks = 0;
	int zone_high_ticks = 0;
	double extreme_volume = 0;
	double extreme_delta = 0;
	double zone_volume = 0;
	bool anchored = false;
};

inline int floor_div(int a, int b) {
	if (b < 1)
		b = 1;
	int q = a / b;
	int r = a % b;
	if (r != 0 && a < 0)
		--q;
	return q;
}

inline int bucket_of(int price_ticks, int ticks_per_level) {
	return floor_div(price_ticks, ticks_per_level < 1 ? 1 : ticks_per_level);
}

inline bool ask_dominant(double bid, double ask, double ratio) {
	if (ratio <= 0)
		return true;
	if (ask <= 0)
		return false;
	if (bid <= 0)
		return ask > 0;
	return ask >= bid * ratio;
}

inline bool bid_dominant(double bid, double ask, double ratio) {
	if (ratio <= 0)
		return true;
	if (bid <= 0)
		return false;
	if (ask <= 0)
		return bid > 0;
	return bid >= ask * ratio;
}

// src must be sorted by price_ticks ascending. Merges into ticks-per-level buckets.
inline int group_vap(
	const VapLevel* src,
	int nsrc,
	int ticks_per_level,
	GroupedLevel* dst,
	int maxdst,
	bool* truncated = nullptr
) {
	if (truncated != nullptr)
		*truncated = false;
	if (src == nullptr || dst == nullptr || nsrc <= 0 || maxdst <= 0)
		return 0;
	if (ticks_per_level < 1)
		ticks_per_level = 1;

	int ndst = 0;
	GroupedLevel cur;
	bool have = false;
	for (int i = 0; i < nsrc; ++i) {
		const int bucket = bucket_of(src[i].price_ticks, ticks_per_level);
		if (!have) {
			cur.bucket = bucket;
			cur.bid = src[i].bid;
			cur.ask = src[i].ask;
			have = true;
			continue;
		}
		if (bucket == cur.bucket) {
			cur.bid += src[i].bid;
			cur.ask += src[i].ask;
			continue;
		}
		if (ndst >= maxdst) {
			if (truncated != nullptr)
				*truncated = true;
			return ndst;
		}
		dst[ndst++] = cur;
		cur.bucket = bucket;
		cur.bid = src[i].bid;
		cur.ask = src[i].ask;
	}
	if (have) {
		if (ndst < maxdst) {
			dst[ndst++] = cur;
		} else if (truncated != nullptr) {
			*truncated = true;
		}
	}
	return ndst;
}

inline double bar_delta_from_vap(const VapLevel* src, int n) {
	double delta = 0;
	if (src == nullptr)
		return 0;
	for (int i = 0; i < n; ++i)
		delta += src[i].delta();
	return delta;
}

inline bool should_reset_persistents(bool full_recalc, int index, int update_start_index) {
	return full_recalc && index == update_start_index;
}

inline bool trigger_bar_ok(int index, int armed_bar, int armed_dir) {
	return armed_dir != 0 && armed_bar >= 0 && index > armed_bar;
}

inline bool arm_in_lifetime(int index, int armed_bar, int lifetime_bars) {
	if (lifetime_bars < 1)
		lifetime_bars = 1;
	return index - armed_bar <= lifetime_bars;
}

inline bool can_trigger(int index, int armed_bar, int armed_dir, int lifetime_bars) {
	return trigger_bar_ok(index, armed_bar, armed_dir)
		&& arm_in_lifetime(index, armed_bar, lifetime_bars);
}

// Walk from the extreme. Long = lowest buckets, ask-dominant. Short = highest, bid-dominant.
// Consecutive buckets only; a gap ends the stack.
inline StackResult count_stacked(
	const GroupedLevel* levels,
	int n,
	bool is_short,
	double min_volume,
	double imbalance_ratio,
	int anchor_tolerance_buckets
) {
	StackResult out;
	if (levels == nullptr || n <= 0)
		return out;
	if (anchor_tolerance_buckets < 0)
		anchor_tolerance_buckets = 0;

	const int extreme_i = is_short ? (n - 1) : 0;
	const int extreme_bucket = levels[extreme_i].bucket;
	out.extreme_volume = levels[extreme_i].volume();
	out.extreme_delta = levels[extreme_i].delta();
	out.anchored = true;

	int prev_bucket = extreme_bucket;
	const int step = is_short ? -1 : 1;
	for (int i = extreme_i; i >= 0 && i < n; i += step) {
		const int dist = is_short
			? (extreme_bucket - levels[i].bucket)
			: (levels[i].bucket - extreme_bucket);
		if (dist > anchor_tolerance_buckets)
			break;
		if (i != extreme_i && levels[i].bucket != prev_bucket + step)
			break;
		if (levels[i].volume() < min_volume)
			break;
		const bool dominant = is_short
			? bid_dominant(levels[i].bid, levels[i].ask, imbalance_ratio)
			: ask_dominant(levels[i].bid, levels[i].ask, imbalance_ratio);
		if (!dominant)
			break;
		++out.stacked;
		out.zone_volume += levels[i].volume();
		if (out.stacked == 1) {
			out.zone_low_bucket = levels[i].bucket;
			out.zone_high_bucket = levels[i].bucket;
		} else if (levels[i].bucket < out.zone_low_bucket) {
			out.zone_low_bucket = levels[i].bucket;
		} else if (levels[i].bucket > out.zone_high_bucket) {
			out.zone_high_bucket = levels[i].bucket;
		}
		prev_bucket = levels[i].bucket;
	}
	return out;
}

inline int grouped_poc_bucket(const GroupedLevel* levels, int n, bool prefer_high) {
	if (levels == nullptr || n <= 0)
		return 0;
	int best_bucket = levels[0].bucket;
	double best_vol = levels[0].volume();
	for (int i = 1; i < n; ++i) {
		const double vol = levels[i].volume();
		if (vol > best_vol + 1e-12) {
			best_vol = vol;
			best_bucket = levels[i].bucket;
			continue;
		}
		if (std::fabs(vol - best_vol) <= 1e-12) {
			if (prefer_high && levels[i].bucket > best_bucket)
				best_bucket = levels[i].bucket;
			if (!prefer_high && levels[i].bucket < best_bucket)
				best_bucket = levels[i].bucket;
		}
	}
	return best_bucket;
}

inline int grouped_poc_ticks(int bucket, int scale) {
	if (scale < 1)
		scale = 1;
	return bucket * scale + scale / 2;
}

inline void fill_grouped_poc(StackResult* st, const GroupedLevel* levels, int n, bool is_short) {
	if (st == nullptr)
		return;
	st->poc_bucket = grouped_poc_bucket(levels, n, is_short);
	st->poc_ticks = grouped_poc_ticks(st->poc_bucket, st->scale);
	if (st->stacked <= 0)
		return;
	const int scale = st->scale < 1 ? 1 : st->scale;
	st->zone_low_ticks = st->zone_low_bucket * scale;
	st->zone_high_ticks = (st->zone_high_bucket + 1) * scale - 1;
	if (st->zone_high_ticks < st->zone_low_ticks)
		st->zone_high_ticks = st->zone_low_ticks;
}

// Try grouping 1–4 plus the user scale when it is outside 1–4.
// Keep the largest stack; a tie uses the tighter scale. Anchor is in
// grouped-bucket units. POC is the max-volume grouped bucket at the
// winning scale, converted back to ticks at bucket center.
inline StackResult count_stacked_multiscale(
	const VapLevel* src,
	int nsrc,
	int user_ticks,
	bool is_short,
	double min_volume,
	double imbalance_ratio,
	int anchor_buckets,
	int min_stacked,
	GroupedLevel* scratch,
	int scratch_max
) {
	(void)min_stacked;
	StackResult best;
	int scales[5];
	int nscales = 0;
	for (int s = 1; s <= 4; ++s)
		scales[nscales++] = s;
	if (user_ticks > 4) {
		scales[nscales++] = user_ticks;
	} else if (user_ticks < 1) {
		scales[0] = 1;
	}
	for (int i = 0; i < nscales; ++i) {
		const int ng = group_vap(src, nsrc, scales[i], scratch, scratch_max);
		StackResult st = count_stacked(
			scratch, ng, is_short, min_volume, imbalance_ratio, anchor_buckets);
		st.scale = scales[i];
		fill_grouped_poc(&st, scratch, ng, is_short);
		if (st.stacked > best.stacked)
			best = st;
		else if (st.stacked == best.stacked && st.stacked > 0 && scales[i] < best.scale)
			best = st;
	}
	return best;
}

inline bool lookback_extreme(
	const double* series,
	int index,
	int lookback,
	bool want_high
) {
	if (series == nullptr || index < 0)
		return false;
	if (lookback <= 0)
		return true;
	if (index < lookback)
		return false;
	const double cur = series[index];
	if (want_high) {
		if (cur <= series[index - 1])
			return false;
		for (int i = 2; i <= lookback; ++i) {
			if (cur < series[index - i])
				return false;
		}
		return true;
	}
	if (cur >= series[index - 1])
		return false;
	for (int i = 2; i <= lookback; ++i) {
		if (cur > series[index - i])
			return false;
	}
	return true;
}

// Directional bar-delta filter. Threshold 0 requires a strict sign
// (long > 0, short < 0) so a flat bar cannot pass both sides.
inline bool bar_delta_supports(double ask_minus_bid, double threshold, bool is_short) {
	if (is_short) {
		if (ask_minus_bid >= 0)
			return false;
		return ask_minus_bid <= -threshold;
	}
	if (ask_minus_bid <= 0)
		return false;
	return ask_minus_bid >= threshold;
}

// mode 0 = directional (long +delta, short -delta). mode 1 = |delta| <= threshold.
inline bool bar_delta_filter(
	double ask_minus_bid,
	double threshold,
	bool is_short,
	int mode
) {
	if (mode == 1)
		return threshold <= 0 || std::fabs(ask_minus_bid) <= threshold;
	return bar_delta_supports(ask_minus_bid, threshold, is_short);
}

inline int pick_setup_direction(
	bool long_ok,
	int long_stack,
	bool short_ok,
	int short_stack,
	double bar_delta
) {
	if (long_ok && short_ok) {
		if (long_stack != short_stack)
			return long_stack > short_stack ? 1 : -1;
		return bar_delta < 0 ? -1 : 1;
	}
	if (short_ok)
		return -1;
	if (long_ok)
		return 1;
	return 0;
}

inline int trigger_offset_ticks(int arrow_offset) {
	if (arrow_offset < 0)
		arrow_offset = 0;
	return arrow_offset + 3;
}

inline int poc_price_ticks(const VapLevel* src, int n, bool prefer_high) {
	if (src == nullptr || n <= 0)
		return 0;
	int best = src[0].price_ticks;
	double best_vol = src[0].volume();
	for (int i = 1; i < n; ++i) {
		const double vol = src[i].volume();
		if (vol > best_vol + 1e-12) {
			best_vol = vol;
			best = src[i].price_ticks;
			continue;
		}
		if (std::fabs(vol - best_vol) <= 1e-12) {
			if (prefer_high && src[i].price_ticks > best)
				best = src[i].price_ticks;
			if (!prefer_high && src[i].price_ticks < best)
				best = src[i].price_ticks;
		}
	}
	return best;
}

inline bool poc_isolated_in_wick(
	int poc_ticks,
	double open,
	double close,
	double tick_size,
	bool is_short
) {
	if (tick_size <= 0)
		return false;
	const double poc = static_cast<double>(poc_ticks) * tick_size;
	const double body_high = open > close ? open : close;
	const double body_low = open < close ? open : close;
	if (is_short)
		return poc > body_high;
	return poc < body_low;
}

// pct==0 means the filter is off (always passes).
inline bool poc_in_extreme_range_pct(
	int poc_ticks,
	double high,
	double low,
	double tick_size,
	double pct,
	bool is_short
) {
	if (pct <= 0)
		return true;
	if (tick_size <= 0)
		return false;
	const double range = high - low;
	if (range <= 0)
		return false;
	const double poc = static_cast<double>(poc_ticks) * tick_size;
	const double from_extreme = is_short ? (high - poc) : (poc - low);
	return from_extreme <= range * (pct / 100.0);
}

inline bool opposite_color_close(double open, double close, bool is_short) {
	if (is_short)
		return close < open;
	return close > open;
}

inline bool volume_gate(double volume, double volume_ma, double min_pct, bool enabled) {
	if (!enabled)
		return true;
	if (volume_ma <= 0)
		return true;
	return volume + 1e-9 >= volume_ma * (min_pct / 100.0);
}

inline double scale_threshold(
	double absolute,
	double volume_ma,
	double baseline_volume,
	bool enabled
) {
	if (!enabled || baseline_volume <= 0)
		return absolute;
	double scale = volume_ma / baseline_volume;
	if (scale < 0.15)
		scale = 0.15;
	if (scale > 3.0)
		scale = 3.0;
	return absolute * scale;
}

// mode 0 = absolute contracts, mode 1 = percent of |climax|.
// Short climax is a +max-delta spike; long climax is a -min-delta spike.
inline bool rebound_hit(
	double climax_delta,
	double current_delta,
	bool is_short,
	int rebound_mode,
	double rebound_abs,
	double rebound_pct
) {
	double need = rebound_abs;
	if (rebound_mode == 1) {
		need = std::fabs(climax_delta) * (rebound_pct / 100.0);
	}
	if (need < 0)
		need = 0;
	if (is_short)
		return current_delta <= climax_delta - need;
	return current_delta >= climax_delta + need;
}

inline bool climax_reached(
	double extreme_delta,
	double climax_min,
	bool is_short
) {
	if (climax_min <= 0)
		return true;
	if (is_short)
		return extreme_delta >= climax_min;
	return extreme_delta <= -climax_min;
}

inline bool in_session(int time_seconds, int start_seconds, int end_seconds, bool enabled) {
	if (!enabled)
		return true;
	if (start_seconds == end_seconds)
		return true;
	if (start_seconds < end_seconds)
		return time_seconds >= start_seconds && time_seconds <= end_seconds;
	return time_seconds >= start_seconds || time_seconds <= end_seconds;
}

inline bool extreme_broken(
	double high,
	double low,
	double armed_extreme,
	double tick_size,
	int break_ticks,
	bool is_short
) {
	if (break_ticks <= 0)
		return false;
	const double pad = static_cast<double>(break_ticks) * tick_size;
	if (is_short)
		return high > armed_extreme + pad;
	return low < armed_extreme - pad;
}

inline void consider_lowest_price(VapLevel* arr, int* n, int cap, const VapLevel& x) {
	if (arr == nullptr || n == nullptr || cap <= 0)
		return;
	if (*n < cap) {
		arr[(*n)++] = x;
		return;
	}
	int worst = 0;
	for (int i = 1; i < cap; ++i) {
		if (arr[i].price_ticks > arr[worst].price_ticks)
			worst = i;
	}
	if (x.price_ticks < arr[worst].price_ticks)
		arr[worst] = x;
}

inline void consider_highest_price(VapLevel* arr, int* n, int cap, const VapLevel& x) {
	if (arr == nullptr || n == nullptr || cap <= 0)
		return;
	if (*n < cap) {
		arr[(*n)++] = x;
		return;
	}
	int worst = 0;
	for (int i = 1; i < cap; ++i) {
		if (arr[i].price_ticks < arr[worst].price_ticks)
			worst = i;
	}
	if (x.price_ticks > arr[worst].price_ticks)
		arr[worst] = x;
}

inline int merge_extreme_vap(
	const VapLevel* lows,
	int nlow,
	const VapLevel* highs,
	int nhigh,
	VapLevel* dst,
	int maxdst
) {
	if (dst == nullptr || maxdst <= 0)
		return 0;
	int written = 0;
	auto push_unique = [&](const VapLevel& x) {
		if (written >= maxdst)
			return;
		for (int i = 0; i < written; ++i) {
			if (dst[i].price_ticks == x.price_ticks)
				return;
		}
		dst[written++] = x;
	};
	if (lows != nullptr) {
		for (int i = 0; i < nlow; ++i)
			push_unique(lows[i]);
	}
	if (highs != nullptr) {
		for (int i = 0; i < nhigh; ++i)
			push_unique(highs[i]);
	}
	for (int i = 1; i < written; ++i) {
		VapLevel cur = dst[i];
		int j = i;
		while (j > 0 && dst[j - 1].price_ticks > cur.price_ticks) {
			dst[j] = dst[j - 1];
			--j;
		}
		dst[j] = cur;
	}
	return written;
}

}  // namespace orion

/*==========================================================================
	ORION - Absorption Climax

	Setup (arrow): stacked bid/ask absorption at a lookback swing.
	Trigger (point): Numbers Bars max/min delta climax, then rebound on
	the bar's ask-bid delta. Study ID 0 falls back to Ask-Bid, with
	intra-bar max/min on the forming bar.

	Alerts: 1 setup short, 2 setup long, 3 trigger.

	INSTALL
	-------
	1. Copy Orion.cpp into ACS_Source (this file is self-contained):
	     C:\SierraChart\ACS_Source\Orion.cpp
	2. Analysis >> Build Custom Studies DLL
	     File >> Select Files -> Orion.cpp
	     Build >> Remote Build
	3. Analysis >> Studies >> Add Custom Study
	     expand Orion -> Orion - Absorption Climax
	4. Point Max Delta / Min Delta at Numbers Bars Calculated Values
	   subgraphs for maximum and minimum ask-bid difference.
	5. Recalculate. After input-index changes, remove the study and add it again.
==========================================================================*/

SCDLLName("Orion")

namespace {

constexpr int kStatusDrawing = 202609041;



int CmpVapPrice(const void* a, const void* b) {
	const orion::VapLevel* lhs = static_cast<const orion::VapLevel*>(a);
	const orion::VapLevel* rhs = static_cast<const orion::VapLevel*>(b);
	if (lhs->price_ticks < rhs->price_ticks)
		return -1;
	if (lhs->price_ticks > rhs->price_ticks)
		return 1;
	return 0;
}

bool FillVapLevel(const s_VolumeAtPriceV2* vap, orion::VapLevel* out) {
	if (vap == nullptr || out == nullptr)
		return false;
	out->price_ticks = vap->PriceInTicks;
	out->bid = vap->GetBidVolume();
	out->ask = vap->GetAskVolume();
	return true;
}

int CollectVap(
	SCStudyInterfaceRef sc,
	int bar_index,
	orion::VapLevel* out,
	int max_out,
	bool* truncated,
	double* out_delta = nullptr,
	double* out_volume = nullptr
) {
	if (truncated != nullptr)
		*truncated = false;
	if (out_delta != nullptr)
		*out_delta = 0;
	if (out_volume != nullptr)
		*out_volume = 0;
	if (sc.VolumeAtPriceForBars == nullptr || out == nullptr || max_out <= 0)
		return 0;
	if (bar_index < 0)
		return 0;
	if (static_cast<int>(sc.VolumeAtPriceForBars->GetNumberOfBars()) <= bar_index)
		return 0;

	const int n = sc.VolumeAtPriceForBars->GetSizeAtBarIndex(bar_index);
	double acc_delta = 0;
	double acc_volume = 0;
	auto account = [&](const orion::VapLevel& level) {
		acc_delta += level.delta();
		acc_volume += level.volume();
	};

	if (n <= max_out) {
		int written = 0;
		for (int i = 0; i < n; ++i) {
			const s_VolumeAtPriceV2* vap = nullptr;
			if (!sc.VolumeAtPriceForBars->GetVAPElementAtIndex(bar_index, i, &vap))
				continue;
			if (!FillVapLevel(vap, &out[written]))
				continue;
			account(out[written]);
			++written;
		}
		if (out_delta != nullptr)
			*out_delta = acc_delta;
		if (out_volume != nullptr)
			*out_volume = acc_volume;
		if (written > 1)
			qsort(out, static_cast<size_t>(written), sizeof(orion::VapLevel), CmpVapPrice);
		return written;
	}

	if (truncated != nullptr)
		*truncated = true;
	int keep = max_out / 2;
	if (keep < 1)
		keep = 1;
	orion::VapLevel lows[orion::kMaxLevels];
	orion::VapLevel highs[orion::kMaxLevels];
	int nlow = 0;
	int nhigh = 0;
	for (int i = 0; i < n; ++i) {
		const s_VolumeAtPriceV2* vap = nullptr;
		if (!sc.VolumeAtPriceForBars->GetVAPElementAtIndex(bar_index, i, &vap))
			continue;
		orion::VapLevel level;
		if (!FillVapLevel(vap, &level))
			continue;
		account(level);
		orion::consider_lowest_price(lows, &nlow, keep, level);
		orion::consider_highest_price(highs, &nhigh, keep, level);
	}
	if (out_delta != nullptr)
		*out_delta = acc_delta;
	if (out_volume != nullptr)
		*out_volume = acc_volume;
	return orion::merge_extreme_vap(lows, nlow, highs, nhigh, out, max_out);
}

double CollectVapDelta(SCStudyInterfaceRef sc, int bar_index, bool* had_vap) {
	if (had_vap != nullptr)
		*had_vap = false;
	if (sc.VolumeAtPriceForBars == nullptr || bar_index < 0)
		return 0;
	if (static_cast<int>(sc.VolumeAtPriceForBars->GetNumberOfBars()) <= bar_index)
		return 0;
	const int n = sc.VolumeAtPriceForBars->GetSizeAtBarIndex(bar_index);
	if (n <= 0)
		return 0;
	if (had_vap != nullptr)
		*had_vap = true;
	double delta = 0;
	for (int i = 0; i < n; ++i) {
		const s_VolumeAtPriceV2* vap = nullptr;
		if (!sc.VolumeAtPriceForBars->GetVAPElementAtIndex(bar_index, i, &vap) || vap == nullptr)
			continue;
		delta += vap->GetAskVolume() - vap->GetBidVolume();
	}
	return delta;
}

double VolumeMA(SCStudyInterfaceRef sc, int index, int length) {
	if (length < 1)
		length = 1;
	double sum = 0;
	int n = 0;
	const int start = index - length + 1;
	for (int i = (start < 0 ? 0 : start); i <= index; ++i) {
		sum += sc.Volume[i];
		++n;
	}
	if (n <= 0)
		return 0;
	return sum / static_cast<double>(n);
}

bool IsLookbackSwing(SCStudyInterfaceRef sc, int index, int lookback, bool is_high) {
	if (index < 0)
		return false;
	if (lookback <= 0)
		lookback = 1;
	if (index < lookback)
		return false;
	if (is_high) {
		if (sc.High[index] <= sc.High[index - 1])
			return false;
		for (int i = 2; i <= lookback; ++i) {
			if (sc.High[index] < sc.High[index - i])
				return false;
		}
		return true;
	}
	if (sc.Low[index] >= sc.Low[index - 1])
		return false;
	for (int i = 2; i <= lookback; ++i) {
		if (sc.Low[index] > sc.Low[index - i])
			return false;
	}
	return true;
}

bool UsableNumber(float value) {
	if (value == 0)
		return true;
	if (value >= static_cast<float>(MAX_CHART_DATA_VALUE) * 0.5f)
		return false;
	if (value <= static_cast<float>(-MAX_CHART_DATA_VALUE) * 0.5f)
		return false;
	return true;
}

float DeltaAt(
	SCStudyInterfaceRef sc,
	const SCFloatArray& array,
	int index,
	bool have_array
) {
	if (have_array) {
		if (array.GetArraySize() > index) {
			const float value = array[index];
			if (UsableNumber(value))
				return value;
		}
		return 0;
	}
	return static_cast<float>(sc.AskVolume[index] - sc.BidVolume[index]);
}

}  // namespace

SCSFExport scsf_OrionAbsorptionClimax(SCStudyInterfaceRef sc) {
	SCSubgraphRef SetupLong = sc.Subgraph[0];
	SCSubgraphRef SetupShort = sc.Subgraph[1];
	SCSubgraphRef TriggerLong = sc.Subgraph[2];
	SCSubgraphRef TriggerShort = sc.Subgraph[3];
	SCSubgraphRef ZoneHigh = sc.Subgraph[4];
	SCSubgraphRef ZoneLow = sc.Subgraph[5];
	SCSubgraphRef Status = sc.Subgraph[6];

	SCInputRef InTicksPerLevel = sc.Input[0];
	SCInputRef InMinVolPerLevel = sc.Input[1];
	SCInputRef InImbalanceRatio = sc.Input[2];
	SCInputRef InMinStacked = sc.Input[3];
	SCInputRef InAnchorTicks = sc.Input[4];
	SCInputRef InExtremeVolPct = sc.Input[5];

	SCInputRef InDeltaThreshold = sc.Input[6];
	SCInputRef InDeltaMode = sc.Input[7];
	SCInputRef InRequirePocWick = sc.Input[8];
	SCInputRef InPocRangePct = sc.Input[9];
	SCInputRef InRequireOppClose = sc.Input[10];

	SCInputRef InSwingBars = sc.Input[11];
	SCInputRef InSessionFilter = sc.Input[12];
	SCInputRef InSessionStart = sc.Input[13];
	SCInputRef InSessionEnd = sc.Input[14];
	SCInputRef InSetupOnClose = sc.Input[15];

	SCInputRef InArrowOffset = sc.Input[16];
	SCInputRef InArrowSize = sc.Input[17];
	SCInputRef InSetupAlert = sc.Input[18];

	SCInputRef InEnableTrigger = sc.Input[19];
	SCInputRef InMaxDelta = sc.Input[20];
	SCInputRef InMinDelta = sc.Input[21];
	SCInputRef InLifetimeBars = sc.Input[22];
	SCInputRef InInvalidateTicks = sc.Input[23];
	SCInputRef InReboundMode = sc.Input[24];
	SCInputRef InReboundAbs = sc.Input[25];
	SCInputRef InReboundPct = sc.Input[26];
	SCInputRef InClimaxMin = sc.Input[27];
	SCInputRef InTriggerSize = sc.Input[28];
	SCInputRef InTriggerAlert = sc.Input[29];

	SCInputRef InRelVolumeGate = sc.Input[30];
	SCInputRef InVolumeMALen = sc.Input[31];
	SCInputRef InMinVolVsMAPct = sc.Input[32];
	SCInputRef InScaleWithMA = sc.Input[33];
	SCInputRef InBaselineVolume = sc.Input[34];
	SCInputRef InLogSignals = sc.Input[35];
	SCInputRef InVersion = sc.Input[36];
	SCInputRef InShowStatus = sc.Input[37];
	SCInputRef InShowZone = sc.Input[38];

	if (sc.SetDefaults) {
		sc.GraphName = "Orion - Absorption Climax";
		sc.StudyDescription =
			"Stacked absorption setup at a swing, then a max/min delta climax "
			"rebound trigger. Relative volume gate and MA-scaled thresholds "
			"are for thin sessions.";
		sc.AutoLoop = 1;
		sc.GraphRegion = 0;
		sc.CalculationPrecedence = LOW_PREC_LEVEL;
		sc.UpdateAlways = 1;
		sc.DrawZeros = 0;
		sc.FreeDLL = 0;
		sc.MaintainVolumeAtPriceData = 1;
		sc.ScaleRangeType = SCALE_SAMEASREGION;

		SetupLong.Name = "Setup Long";
		SetupLong.DrawStyle = DRAWSTYLE_ARROW_UP;
		SetupLong.PrimaryColor = RGB(46, 230, 163);
		SetupLong.LineWidth = 2;
		SetupLong.DrawZeros = false;

		SetupShort.Name = "Setup Short";
		SetupShort.DrawStyle = DRAWSTYLE_ARROW_DOWN;
		SetupShort.PrimaryColor = RGB(255, 92, 122);
		SetupShort.LineWidth = 2;
		SetupShort.DrawZeros = false;

		TriggerLong.Name = "Trigger Long";
		TriggerLong.DrawStyle = DRAWSTYLE_POINT;
		TriggerLong.PrimaryColor = RGB(0, 255, 255);
		TriggerLong.LineWidth = 5;
		TriggerLong.DrawZeros = false;

		TriggerShort.Name = "Trigger Short";
		TriggerShort.DrawStyle = DRAWSTYLE_POINT;
		TriggerShort.PrimaryColor = RGB(255, 165, 0);
		TriggerShort.LineWidth = 5;
		TriggerShort.DrawZeros = false;

		ZoneHigh.Name = "Zone High";
		ZoneHigh.DrawStyle = DRAWSTYLE_TRANSPARENT_FILL_RECTANGLE_TOP;
		ZoneHigh.PrimaryColor = RGB(70, 110, 150);
		ZoneHigh.DrawZeros = false;

		ZoneLow.Name = "Zone Low";
		ZoneLow.DrawStyle = DRAWSTYLE_TRANSPARENT_FILL_RECTANGLE_BOTTOM;
		ZoneLow.PrimaryColor = RGB(70, 110, 150);
		ZoneLow.DrawZeros = false;

		Status.Name = "Status";
		Status.DrawStyle = DRAWSTYLE_IGNORE;
		Status.PrimaryColor = RGB(220, 220, 220);
		Status.LineWidth = 12;
		Status.DrawZeros = false;

		unsigned short order = 1;

		InTicksPerLevel.Name = "[Grouping] Ticks per level (also tries 1-4)";
		InTicksPerLevel.SetInt(2);
		InTicksPerLevel.SetIntLimits(1, 100);
		InTicksPerLevel.DisplayOrder = order++;

		InMinVolPerLevel.Name = "[Absorption] Min volume per level";
		InMinVolPerLevel.SetInt(25);
		InMinVolPerLevel.SetIntLimits(0, 1000000);
		InMinVolPerLevel.DisplayOrder = order++;

		InImbalanceRatio.Name = "[Absorption] Imbalance ratio % (300 = 3:1)";
		InImbalanceRatio.SetInt(300);
		InImbalanceRatio.SetIntLimits(100, 2000);
		InImbalanceRatio.DisplayOrder = order++;

		InMinStacked.Name = "[Absorption] Min stacked levels";
		InMinStacked.SetInt(2);
		InMinStacked.SetIntLimits(1, 20);
		InMinStacked.DisplayOrder = order++;

		InAnchorTicks.Name = "[Absorption] Anchor tolerance (grouped buckets)";
		InAnchorTicks.SetInt(3);
		InAnchorTicks.SetIntLimits(0, 20);
		InAnchorTicks.DisplayOrder = order++;

		InExtremeVolPct.Name = "[Absorption] Min % of bar volume at extreme (0=off)";
		InExtremeVolPct.SetInt(0);
		InExtremeVolPct.SetIntLimits(0, 100);
		InExtremeVolPct.DisplayOrder = order++;

		InDeltaThreshold.Name = "[Exhaustion] Bar delta threshold (0 = sign only)";
		InDeltaThreshold.SetInt(0);
		InDeltaThreshold.SetIntLimits(0, 100000);
		InDeltaThreshold.DisplayOrder = order++;

		InDeltaMode.Name = "[Exhaustion] Bar delta filter";
		InDeltaMode.SetCustomInputStrings("Directional (sign/min);Magnitude cap |delta|<=");
		InDeltaMode.SetCustomInputIndex(0);
		InDeltaMode.DisplayOrder = order++;

		InRequirePocWick.Name = "[Exhaustion] Require POC isolated in wick";
		InRequirePocWick.SetYesNo(false);
		InRequirePocWick.DisplayOrder = order++;

		InPocRangePct.Name = "[Exhaustion] POC in extreme X% of range (0=off)";
		InPocRangePct.SetInt(50);
		InPocRangePct.SetIntLimits(0, 100);
		InPocRangePct.DisplayOrder = order++;

		InRequireOppClose.Name = "[Exhaustion] Require opposite-color close";
		InRequireOppClose.SetYesNo(false);
		InRequireOppClose.DisplayOrder = order++;

		InSwingBars.Name = "[Context] Lookback bars to confirm high/low";
		InSwingBars.SetInt(8);
		InSwingBars.SetIntLimits(1, 200);
		InSwingBars.DisplayOrder = order++;

		InSessionFilter.Name = "[Context] Enable session filter";
		InSessionFilter.SetYesNo(false);
		InSessionFilter.DisplayOrder = order++;

		InSessionStart.Name = "[Context] Session start (chart time)";
		InSessionStart.SetTime(HMS_TIME(9, 30, 0));
		InSessionStart.DisplayOrder = order++;

		InSessionEnd.Name = "[Context] Session end (chart time)";
		InSessionEnd.SetTime(HMS_TIME(16, 0, 0));
		InSessionEnd.DisplayOrder = order++;

		InSetupOnClose.Name = "[Context] Setup only on bar close";
		InSetupOnClose.SetYesNo(true);
		InSetupOnClose.DisplayOrder = order++;

		InArrowOffset.Name = "[Display] Setup arrow offset (ticks)";
		InArrowOffset.SetInt(6);
		InArrowOffset.SetIntLimits(0, 200);
		InArrowOffset.DisplayOrder = order++;

		InArrowSize.Name = "[Display] Setup arrow size";
		InArrowSize.SetInt(2);
		InArrowSize.SetIntLimits(1, 50);
		InArrowSize.DisplayOrder = order++;

		InSetupAlert.Name = "[Display] Alert on setup";
		InSetupAlert.SetYesNo(false);
		InSetupAlert.DisplayOrder = order++;

		InEnableTrigger.Name = "[Trigger] Enable climax trigger";
		InEnableTrigger.SetYesNo(true);
		InEnableTrigger.DisplayOrder = order++;

		InMaxDelta.Name = "[Trigger] Max delta (Numbers Bars subgraph)";
		InMaxDelta.SetStudySubgraphValues(0, 0);
		InMaxDelta.DisplayOrder = order++;

		InMinDelta.Name = "[Trigger] Min delta (Numbers Bars subgraph)";
		InMinDelta.SetStudySubgraphValues(0, 0);
		InMinDelta.DisplayOrder = order++;

		InLifetimeBars.Name = "[Trigger] Armed setup lifetime (bars)";
		InLifetimeBars.SetInt(3);
		InLifetimeBars.SetIntLimits(1, 50);
		InLifetimeBars.DisplayOrder = order++;

		InInvalidateTicks.Name = "[Trigger] Invalidate if extreme breaks (ticks, 0=off)";
		InInvalidateTicks.SetInt(0);
		InInvalidateTicks.SetIntLimits(0, 1000);
		InInvalidateTicks.DisplayOrder = order++;

		InReboundMode.Name = "[Trigger] Rebound mode";
		InReboundMode.SetCustomInputStrings("Absolute contracts;Percent of climax");
		InReboundMode.SetCustomInputIndex(1);
		InReboundMode.DisplayOrder = order++;

		InReboundAbs.Name = "[Trigger] Rebound min (contracts)";
		InReboundAbs.SetInt(100);
		InReboundAbs.SetIntLimits(1, 1000000);
		InReboundAbs.DisplayOrder = order++;

		InReboundPct.Name = "[Trigger] Rebound min (% of climax)";
		InReboundPct.SetInt(50);
		InReboundPct.SetIntLimits(1, 100);
		InReboundPct.DisplayOrder = order++;

		InClimaxMin.Name = "[Trigger] Climax min (contracts, 0=off)";
		InClimaxMin.SetInt(0);
		InClimaxMin.SetIntLimits(0, 1000000);
		InClimaxMin.DisplayOrder = order++;

		InTriggerSize.Name = "[Trigger] Trigger marker size";
		InTriggerSize.SetInt(5);
		InTriggerSize.SetIntLimits(1, 50);
		InTriggerSize.DisplayOrder = order++;

		InTriggerAlert.Name = "[Trigger] Alert on trigger";
		InTriggerAlert.SetYesNo(true);
		InTriggerAlert.DisplayOrder = order++;

		InRelVolumeGate.Name = "[Filter] Skip bars below volume MA";
		InRelVolumeGate.SetYesNo(false);
		InRelVolumeGate.DisplayOrder = order++;

		InVolumeMALen.Name = "[Filter] Volume MA length";
		InVolumeMALen.SetInt(50);
		InVolumeMALen.SetIntLimits(2, 500);
		InVolumeMALen.DisplayOrder = order++;

		InMinVolVsMAPct.Name = "[Filter] Min volume vs MA %";
		InMinVolVsMAPct.SetInt(50);
		InMinVolVsMAPct.SetIntLimits(0, 200);
		InMinVolVsMAPct.DisplayOrder = order++;

		InScaleWithMA.Name = "[Filter] Scale volume/delta/climax/rebound with volume MA";
		InScaleWithMA.SetYesNo(false);
		InScaleWithMA.DisplayOrder = order++;

		InBaselineVolume.Name = "[Filter] Baseline volume for scaling";
		InBaselineVolume.SetFloat(100);
		InBaselineVolume.SetFloatLimits(1, 1000000);
		InBaselineVolume.DisplayOrder = order++;

		InLogSignals.Name = "[Display] Log signals to Message Log";
		InLogSignals.SetYesNo(false);
		InLogSignals.DisplayOrder = order++;

		InShowStatus.Name = "[Display] Show arm status";
		InShowStatus.SetYesNo(true);
		InShowStatus.DisplayOrder = order++;

		InShowZone.Name = "[Display] Show absorption zone";
		InShowZone.SetYesNo(true);
		InShowZone.DisplayOrder = order++;

		InVersion.Name = "Do not change (study version)";
		InVersion.SetInt(3);
		InVersion.SetIntLimits(2, 3);
		InVersion.DisplayOrder = order++;

		return;
	}

	if (sc.MaintainVolumeAtPriceData == 0) {
		sc.MaintainVolumeAtPriceData = 1;
		sc.FlagToReloadChartData = 1;
		return;
	}

	int& armed_dir = sc.GetPersistentInt(1);
	int& armed_bar = sc.GetPersistentInt(2);
	int& climax_seen = sc.GetPersistentInt(3);
	int& fbar_index = sc.GetPersistentInt(4);
	int& triggered_bar = sc.GetPersistentInt(5);
	int& vap_truncated_logged = sc.GetPersistentInt(6);
	int& arm_stacked = sc.GetPersistentInt(7);
	int& arm_scale = sc.GetPersistentInt(8);
	int& mixed_delta_logged = sc.GetPersistentInt(9);

	float& armed_px = sc.GetPersistentFloat(1);
	float& climax_val = sc.GetPersistentFloat(2);
	float& fbar_max = sc.GetPersistentFloat(3);
	float& fbar_min = sc.GetPersistentFloat(4);
	float& zone_high = sc.GetPersistentFloat(5);
	float& zone_low = sc.GetPersistentFloat(6);

	auto disarm = [&]() {
		armed_dir = 0;
		armed_bar = -1;
		climax_seen = 0;
		climax_val = 0;
		arm_stacked = 0;
		arm_scale = 0;
		armed_px = 0;
		zone_high = 0;
		zone_low = 0;
	};

	if (orion::should_reset_persistents(
			sc.IsFullRecalculation != 0, sc.Index, sc.UpdateStartIndex)) {
		disarm();
		fbar_index = -1;
		triggered_bar = -1;
		vap_truncated_logged = 0;
		mixed_delta_logged = 0;
		fbar_max = 0;
		fbar_min = 0;
	}

	const int index = sc.Index;
	const int last_index = sc.ArraySize - 1;
	const double tick_size = sc.TickSize;
	if (tick_size <= 0)
		return;

	const int arrow_size = InArrowSize.GetInt() < 1 ? 1 : InArrowSize.GetInt();
	const int trigger_size = InTriggerSize.GetInt() < 1 ? 1 : InTriggerSize.GetInt();
	SetupLong.LineWidth = static_cast<unsigned short>(arrow_size);
	SetupShort.LineWidth = static_cast<unsigned short>(arrow_size);
	TriggerLong.LineWidth = static_cast<unsigned short>(trigger_size);
	TriggerShort.LineWidth = static_cast<unsigned short>(trigger_size);
	Status.LineWidth = 12;

	if (triggered_bar != index) {
		TriggerLong[index] = 0;
		TriggerShort[index] = 0;
		ZoneHigh[index] = 0;
		ZoneLow[index] = 0;
	}
	SetupLong[index] = 0;
	SetupShort[index] = 0;

	if (InShowZone.GetYesNo()) {
		ZoneHigh.DrawStyle = DRAWSTYLE_TRANSPARENT_FILL_RECTANGLE_TOP;
		ZoneLow.DrawStyle = DRAWSTYLE_TRANSPARENT_FILL_RECTANGLE_BOTTOM;
	} else {
		ZoneHigh.DrawStyle = DRAWSTYLE_IGNORE;
		ZoneLow.DrawStyle = DRAWSTYLE_IGNORE;
	}
	Status.DrawStyle = DRAWSTYLE_IGNORE;

	SCFloatArray max_delta_arr;
	SCFloatArray min_delta_arr;
	const bool max_wired = InMaxDelta.GetStudyID() != 0;
	const bool min_wired = InMinDelta.GetStudyID() != 0;
	if (max_wired != min_wired && mixed_delta_logged == 0) {
		sc.AddMessageToLog("Orion: wire both Max delta and Min delta, or neither", 1);
		mixed_delta_logged = 1;
	}
	const bool have_max = max_wired && min_wired
		&& sc.GetStudyArrayUsingID(InMaxDelta.GetStudyID(), InMaxDelta.GetSubgraphIndex(), max_delta_arr) != 0;
	const bool have_min = max_wired && min_wired
		&& sc.GetStudyArrayUsingID(InMinDelta.GetStudyID(), InMinDelta.GetSubgraphIndex(), min_delta_arr) != 0;
	const bool have_both = have_max && have_min;

	const int ticks_per_level = InTicksPerLevel.GetInt();
	const double imbalance_ratio = static_cast<double>(InImbalanceRatio.GetInt()) / 100.0;
	const int min_stacked = InMinStacked.GetInt();
	const int swing_bars = InSwingBars.GetInt();
	const bool bar_closed = sc.GetBarHasClosedStatus(index) == BHCS_BAR_HAS_CLOSED;
	const bool forming = (index == last_index) && !bar_closed;

	float max_delta = DeltaAt(sc, max_delta_arr, index, have_both);
	float min_delta = DeltaAt(sc, min_delta_arr, index, have_both);
	if (!have_both) {
		const float bar_d = static_cast<float>(sc.AskVolume[index] - sc.BidVolume[index]);
		if (forming) {
			if (fbar_index != index) {
				fbar_index = index;
				fbar_max = bar_d;
				fbar_min = bar_d;
			}
			if (bar_d > fbar_max)
				fbar_max = bar_d;
			if (bar_d < fbar_min)
				fbar_min = bar_d;
			max_delta = fbar_max;
			min_delta = fbar_min;
		} else {
			max_delta = bar_d > 0 ? bar_d : 0;
			min_delta = bar_d < 0 ? bar_d : 0;
		}
	}

	const bool allow_setup = !InSetupOnClose.GetYesNo() || bar_closed;
	const int bar_time = sc.BaseDateTimeIn[index].GetTimeInSeconds();
	const bool session_ok = orion::in_session(
		bar_time, InSessionStart.GetTime(), InSessionEnd.GetTime(),
		InSessionFilter.GetYesNo() != 0);

	const bool scale_on = InScaleWithMA.GetYesNo() != 0;
	const bool gate_on = InRelVolumeGate.GetYesNo() != 0;
	const double vol_ma = (gate_on || scale_on)
		? VolumeMA(sc, index, InVolumeMALen.GetInt())
		: 0;
	const bool vol_ok = orion::volume_gate(
		sc.Volume[index], vol_ma, InMinVolVsMAPct.GetInt(), gate_on);

	const double min_vol = orion::scale_threshold(
		InMinVolPerLevel.GetInt(), vol_ma, InBaselineVolume.GetFloat(), scale_on);
	const double delta_thresh = orion::scale_threshold(
		InDeltaThreshold.GetInt(), vol_ma, InBaselineVolume.GetFloat(), scale_on);
	const double climax_min = orion::scale_threshold(
		InClimaxMin.GetInt(), vol_ma, InBaselineVolume.GetFloat(), scale_on);
	const double rebound_abs = orion::scale_threshold(
		InReboundAbs.GetInt(), vol_ma, InBaselineVolume.GetFloat(), scale_on);

	const bool broken = armed_dir != 0
		&& orion::extreme_broken(
			sc.High[index], sc.Low[index], armed_px, tick_size,
			InInvalidateTicks.GetInt(), armed_dir < 0);
	const bool expired = armed_dir != 0
		&& !orion::arm_in_lifetime(index, armed_bar, InLifetimeBars.GetInt());
	const bool in_window = orion::can_trigger(
		index, armed_bar, armed_dir, InLifetimeBars.GetInt());

	const bool want_setup = allow_setup && session_ok && vol_ok;
	const bool want_trigger_delta = InEnableTrigger.GetYesNo() && in_window;

	orion::VapLevel vap[orion::kMaxLevels];
	int nvap = 0;
	bool vap_truncated = false;
	const double sc_bar_delta = static_cast<double>(sc.AskVolume[index] - sc.BidVolume[index]);
	double bar_delta = sc_bar_delta;
	double vap_volume = 0;
	if (want_setup) {
		double vap_delta = 0;
		nvap = CollectVap(
			sc, index, vap, orion::kMaxLevels, &vap_truncated, &vap_delta, &vap_volume);
		if (vap_truncated && vap_truncated_logged == 0) {
			sc.AddMessageToLog("Orion: volume-at-price truncated; keeping high/low extremes", 1);
			vap_truncated_logged = 1;
		}
		if (nvap > 0)
			bar_delta = vap_delta;
	} else if (want_trigger_delta) {
		bool had_vap = false;
		const double vap_delta = CollectVapDelta(sc, index, &had_vap);
		if (had_vap)
			bar_delta = vap_delta;
	}

	bool did_trigger = false;
	if (InEnableTrigger.GetYesNo() && in_window) {
		const bool is_short = armed_dir < 0;
		const float extreme_delta = is_short ? max_delta : min_delta;
		bool ready = true;
		if (!climax_seen) {
			if (!orion::climax_reached(extreme_delta, climax_min, is_short))
				ready = false;
			else {
				climax_seen = 1;
				climax_val = extreme_delta;
			}
		} else {
			if (is_short && extreme_delta > climax_val)
				climax_val = extreme_delta;
			if (!is_short && extreme_delta < climax_val)
				climax_val = extreme_delta;
		}
		if (ready && orion::rebound_hit(
				climax_val, static_cast<float>(bar_delta), is_short,
				InReboundMode.GetIndex(),
				rebound_abs,
				InReboundPct.GetInt())) {
			const double trig_off =
				orion::trigger_offset_ticks(InArrowOffset.GetInt()) * tick_size;
			if (is_short)
				TriggerShort[index] = static_cast<float>(sc.High[index] + trig_off);
			else
				TriggerLong[index] = static_cast<float>(sc.Low[index] - trig_off);
			triggered_bar = index;
			did_trigger = true;
			if (InTriggerAlert.GetYesNo() && index >= last_index - 1 && !sc.IsFullRecalculation) {
				SCString msg;
				msg.Format(
					"ORION TRIGGER %s climax=%.0f delta=%.0f stack=%d scale=%d",
					is_short ? "SHORT" : "LONG",
					climax_val, bar_delta, arm_stacked, arm_scale);
				sc.SetAlert(3, index, msg);
				if (InLogSignals.GetYesNo())
					sc.AddMessageToLog(msg, 0);
			}
		}
	}

	if ((did_trigger || armed_dir != 0) && arm_stacked > 0) {
		float zhi = zone_high;
		float zlo = zone_low;
		if (zhi <= zlo)
			zhi = zlo + static_cast<float>(tick_size);
		ZoneHigh[index] = zhi;
		ZoneLow[index] = zlo;
	}

	if (did_trigger || expired || broken)
		disarm();

	if (want_setup && nvap > 0) {
		double bar_volume = vap_volume;
		if (bar_volume <= 0)
			bar_volume = sc.Volume[index];

		orion::GroupedLevel grouped[orion::kMaxLevels];
		orion::StackResult long_stack;
		orion::StackResult short_stack;
		bool long_ok = false;
		bool short_ok = false;

		auto consider = [&](bool is_short) {
			if (!IsLookbackSwing(sc, index, swing_bars, is_short))
				return;
			orion::StackResult stack = orion::count_stacked_multiscale(
				vap, nvap, ticks_per_level, is_short, min_vol, imbalance_ratio,
				InAnchorTicks.GetInt(), min_stacked, grouped, orion::kMaxLevels);
			if (stack.stacked < min_stacked)
				return;
			if (InExtremeVolPct.GetInt() > 0 && bar_volume > 0) {
				if (stack.extreme_volume * 100.0 < bar_volume * InExtremeVolPct.GetInt())
					return;
			}
			if (!orion::bar_delta_filter(
					bar_delta, delta_thresh, is_short, InDeltaMode.GetIndex()))
				return;
			if (InRequirePocWick.GetYesNo()
				&& !orion::poc_isolated_in_wick(
					stack.poc_ticks, sc.Open[index], sc.Close[index], tick_size, is_short))
				return;
			if (!orion::poc_in_extreme_range_pct(
					stack.poc_ticks, sc.High[index], sc.Low[index], tick_size,
					InPocRangePct.GetInt(), is_short))
				return;
			if (InRequireOppClose.GetYesNo()
				&& !orion::opposite_color_close(sc.Open[index], sc.Close[index], is_short))
				return;
			if (is_short) {
				short_ok = true;
				short_stack = stack;
			} else {
				long_ok = true;
				long_stack = stack;
			}
		};

		consider(false);
		consider(true);
		const int dir = orion::pick_setup_direction(
			long_ok, long_stack.stacked, short_ok, short_stack.stacked, bar_delta);
		if (dir != 0) {
			const bool is_short = dir < 0;
			const orion::StackResult& stack = is_short ? short_stack : long_stack;
			const double offset = InArrowOffset.GetInt() * tick_size;
			const bool new_arm = armed_bar != index || armed_dir != dir;
			armed_dir = dir;
			armed_bar = index;
			climax_seen = 0;
			climax_val = 0;
			arm_stacked = stack.stacked;
			arm_scale = stack.scale;
			zone_low = static_cast<float>(stack.zone_low_ticks * tick_size);
			zone_high = static_cast<float>(stack.zone_high_ticks * tick_size);
			if (zone_high < zone_low) {
				const float tmp = zone_high;
				zone_high = zone_low;
				zone_low = tmp;
			}
			if (is_short) {
				armed_px = sc.High[index];
				SetupShort[index] = static_cast<float>(sc.High[index] + offset);
			} else {
				armed_px = sc.Low[index];
				SetupLong[index] = static_cast<float>(sc.Low[index] - offset);
			}
			if (arm_stacked > 0) {
				float zhi = zone_high;
				float zlo = zone_low;
				if (zhi <= zlo)
					zhi = zlo + static_cast<float>(tick_size);
				ZoneHigh[index] = zhi;
				ZoneLow[index] = zlo;
			}
			if (new_arm && InSetupAlert.GetYesNo()
				&& index >= last_index - 1 && !sc.IsFullRecalculation) {
				SCString msg;
				msg.Format(
					"ORION SETUP %s stack=%d scale=%d",
					is_short ? "SHORT" : "LONG", arm_stacked, arm_scale);
				sc.SetAlert(is_short ? 1 : 2, index, msg);
				if (InLogSignals.GetYesNo())
					sc.AddMessageToLog(msg, 0);
			}
		}
	}

	if (index == last_index) {
		if (!InShowStatus.GetYesNo()) {
			sc.DeleteACSChartDrawing(sc.ChartNumber, TOOL_DELETE_CHARTDRAWING, kStatusDrawing);
		} else {
			SCString text;
			if (armed_dir != 0) {
				const int age = index - armed_bar;
				const int life = InLifetimeBars.GetInt() < 1 ? 1 : InLifetimeBars.GetInt();
				text.Format(
					"ORION %s  %d/%d  stack %d @ %d  %s",
					armed_dir < 0 ? "SHORT" : "LONG",
					age, life, arm_stacked, arm_scale,
					climax_seen ? "climax" : "wait");
			} else {
				text = "ORION";
			}
			s_UseTool tool;
			tool.Clear();
			tool.ChartNumber = sc.ChartNumber;
			tool.DrawingType = DRAWING_TEXT;
			tool.LineNumber = kStatusDrawing;
			tool.AddMethod = UTAM_ADD_OR_ADJUST;
			tool.Region = sc.GraphRegion;
			tool.BeginDateTime = 1;
			tool.BeginValue = 6;
			tool.UseRelativeVerticalValues = 1;
			tool.Color = Status.PrimaryColor;
			tool.FontSize = Status.LineWidth > 0 ? Status.LineWidth : 12;
			tool.FontBold = 1;
			tool.Text = text;
			tool.AddAsUserDrawnDrawing = 0;
			tool.DrawUnderneathMainGraph = 0;
			sc.UseTool(tool);
		}
	}
}

