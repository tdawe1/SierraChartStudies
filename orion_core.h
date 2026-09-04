#pragma once

// Order-flow helpers for Orion. No Sierra Chart types.

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
