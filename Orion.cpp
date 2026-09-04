#include "sierrachart.h"
#include "orion_core.h"

#include <cstdlib>

/*==========================================================================
	ORION - Absorption Climax

	Setup (arrow): stacked bid/ask absorption at a lookback swing.
	Trigger (point): Numbers Bars max/min delta climax, then rebound on
	the bar's ask-bid delta. Study ID 0 falls back to Ask-Bid, with
	intra-bar max/min on the forming bar.

	Alerts: 1 setup short, 2 setup long, 3 trigger.

	INSTALL
	-------
	1. Copy Orion.cpp and orion_core.h into ACS_Source, for example:
	     C:\SierraChart\ACS_Source\
	2. Analysis >> Build Custom Studies DLL
	     File >> Select Files -> Orion.cpp
	     Build >> Remote Build
	3. On a Numbers Bars chart:
	     Analysis >> Studies >> Add Custom Study -> Orion
	     Point Max Delta / Min Delta at Numbers Bars Calculated Values
	     subgraphs for maximum and minimum ask-bid difference.
	4. Recalculate. After input-index changes, remove the study and add it again.
==========================================================================*/

SCDLLName("Orion")

namespace {

int CmpVapPrice(const void* a, const void* b) {
	const orion::VapLevel* lhs = static_cast<const orion::VapLevel*>(a);
	const orion::VapLevel* rhs = static_cast<const orion::VapLevel*>(b);
	if (lhs->price_ticks < rhs->price_ticks)
		return -1;
	if (lhs->price_ticks > rhs->price_ticks)
		return 1;
	return 0;
}

int CollectVap(
	SCStudyInterfaceRef sc,
	int bar_index,
	orion::VapLevel* out,
	int max_out,
	bool* truncated
) {
	if (truncated != nullptr)
		*truncated = false;
	if (sc.VolumeAtPriceForBars == nullptr || out == nullptr || max_out <= 0)
		return 0;
	if (bar_index < 0)
		return 0;
	if (static_cast<int>(sc.VolumeAtPriceForBars->GetNumberOfBars()) <= bar_index)
		return 0;

	const int n = sc.VolumeAtPriceForBars->GetSizeAtBarIndex(bar_index);
	if (truncated != nullptr && n > max_out)
		*truncated = true;
	int written = 0;
	for (int i = 0; i < n && written < max_out; ++i) {
		const s_VolumeAtPriceV2* vap = nullptr;
		if (!sc.VolumeAtPriceForBars->GetVAPElementAtIndex(bar_index, i, &vap) || vap == nullptr)
			continue;
		out[written].price_ticks = vap->PriceInTicks;
		out[written].bid = vap->GetBidVolume();
		out[written].ask = vap->GetAskVolume();
		++written;
	}
	if (written > 1)
		qsort(out, static_cast<size_t>(written), sizeof(orion::VapLevel), CmpVapPrice);
	return written;
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
	if (have_array && array.GetArraySize() > index) {
		const float value = array[index];
		if (UsableNumber(value))
			return value;
	}
	return static_cast<float>(sc.AskVolume[index] - sc.BidVolume[index]);
}

}  // namespace

SCSFExport scsf_OrionAbsorptionClimax(SCStudyInterfaceRef sc) {
	SCSubgraphRef SetupLong = sc.Subgraph[0];
	SCSubgraphRef SetupShort = sc.Subgraph[1];
	SCSubgraphRef TriggerLong = sc.Subgraph[2];
	SCSubgraphRef TriggerShort = sc.Subgraph[3];

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
		SetupLong.PrimaryColor = RGB(0, 220, 0);
		SetupLong.LineWidth = 2;
		SetupLong.DrawZeros = false;

		SetupShort.Name = "Setup Short";
		SetupShort.DrawStyle = DRAWSTYLE_ARROW_DOWN;
		SetupShort.PrimaryColor = RGB(230, 0, 0);
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

		unsigned short order = 1;

		InTicksPerLevel.Name = "Ticks per level (also tries 1-4)";
		InTicksPerLevel.SetInt(2);
		InTicksPerLevel.SetIntLimits(1, 100);
		InTicksPerLevel.DisplayOrder = order++;

		InMinVolPerLevel.Name = "Min volume per level";
		InMinVolPerLevel.SetInt(25);
		InMinVolPerLevel.SetIntLimits(0, 1000000);
		InMinVolPerLevel.DisplayOrder = order++;

		InImbalanceRatio.Name = "Imbalance ratio % (300 = 3:1)";
		InImbalanceRatio.SetInt(300);
		InImbalanceRatio.SetIntLimits(100, 2000);
		InImbalanceRatio.DisplayOrder = order++;

		InMinStacked.Name = "Min stacked levels";
		InMinStacked.SetInt(2);
		InMinStacked.SetIntLimits(1, 20);
		InMinStacked.DisplayOrder = order++;

		InAnchorTicks.Name = "Anchor tolerance (grouped buckets)";
		InAnchorTicks.SetInt(3);
		InAnchorTicks.SetIntLimits(0, 20);
		InAnchorTicks.DisplayOrder = order++;

		InExtremeVolPct.Name = "Min % of bar volume at extreme (0=off)";
		InExtremeVolPct.SetInt(0);
		InExtremeVolPct.SetIntLimits(0, 100);
		InExtremeVolPct.DisplayOrder = order++;

		InDeltaThreshold.Name = "Bar delta threshold (0 = sign only)";
		InDeltaThreshold.SetInt(0);
		InDeltaThreshold.SetIntLimits(0, 100000);
		InDeltaThreshold.DisplayOrder = order++;

		InDeltaMode.Name = "Bar delta filter";
		InDeltaMode.SetCustomInputStrings("Directional (sign/min);Magnitude cap |delta|<=");
		InDeltaMode.SetCustomInputIndex(0);
		InDeltaMode.DisplayOrder = order++;

		InRequirePocWick.Name = "Require POC isolated in wick";
		InRequirePocWick.SetYesNo(false);
		InRequirePocWick.DisplayOrder = order++;

		InPocRangePct.Name = "POC in extreme X% of range (0=off)";
		InPocRangePct.SetInt(50);
		InPocRangePct.SetIntLimits(0, 100);
		InPocRangePct.DisplayOrder = order++;

		InRequireOppClose.Name = "Require opposite-color close";
		InRequireOppClose.SetYesNo(false);
		InRequireOppClose.DisplayOrder = order++;

		InSwingBars.Name = "Lookback bars to confirm high/low";
		InSwingBars.SetInt(8);
		InSwingBars.SetIntLimits(1, 200);
		InSwingBars.DisplayOrder = order++;

		InSessionFilter.Name = "Enable session filter";
		InSessionFilter.SetYesNo(false);
		InSessionFilter.DisplayOrder = order++;

		InSessionStart.Name = "Session start (chart time)";
		InSessionStart.SetTime(HMS_TIME(9, 30, 0));
		InSessionStart.DisplayOrder = order++;

		InSessionEnd.Name = "Session end (chart time)";
		InSessionEnd.SetTime(HMS_TIME(16, 0, 0));
		InSessionEnd.DisplayOrder = order++;

		InSetupOnClose.Name = "Setup only on bar close";
		InSetupOnClose.SetYesNo(true);
		InSetupOnClose.DisplayOrder = order++;

		InArrowOffset.Name = "Setup arrow offset (ticks)";
		InArrowOffset.SetInt(4);
		InArrowOffset.SetIntLimits(0, 200);
		InArrowOffset.DisplayOrder = order++;

		InArrowSize.Name = "Setup arrow size";
		InArrowSize.SetInt(2);
		InArrowSize.SetIntLimits(1, 50);
		InArrowSize.DisplayOrder = order++;

		InSetupAlert.Name = "Alert on setup";
		InSetupAlert.SetYesNo(false);
		InSetupAlert.DisplayOrder = order++;

		InEnableTrigger.Name = "Enable climax trigger";
		InEnableTrigger.SetYesNo(true);
		InEnableTrigger.DisplayOrder = order++;

		InMaxDelta.Name = "Max delta (Numbers Bars subgraph)";
		InMaxDelta.SetStudySubgraphValues(0, 0);
		InMaxDelta.DisplayOrder = order++;

		InMinDelta.Name = "Min delta (Numbers Bars subgraph)";
		InMinDelta.SetStudySubgraphValues(0, 0);
		InMinDelta.DisplayOrder = order++;

		InLifetimeBars.Name = "Armed setup lifetime (bars)";
		InLifetimeBars.SetInt(3);
		InLifetimeBars.SetIntLimits(1, 50);
		InLifetimeBars.DisplayOrder = order++;

		InInvalidateTicks.Name = "Invalidate if extreme breaks (ticks, 0=off)";
		InInvalidateTicks.SetInt(0);
		InInvalidateTicks.SetIntLimits(0, 1000);
		InInvalidateTicks.DisplayOrder = order++;

		InReboundMode.Name = "Rebound mode";
		InReboundMode.SetCustomInputStrings("Absolute contracts;Percent of climax");
		InReboundMode.SetCustomInputIndex(1);
		InReboundMode.DisplayOrder = order++;

		InReboundAbs.Name = "Rebound min (contracts)";
		InReboundAbs.SetInt(100);
		InReboundAbs.SetIntLimits(1, 1000000);
		InReboundAbs.DisplayOrder = order++;

		InReboundPct.Name = "Rebound min (% of climax)";
		InReboundPct.SetInt(50);
		InReboundPct.SetIntLimits(1, 100);
		InReboundPct.DisplayOrder = order++;

		InClimaxMin.Name = "Climax min (contracts, 0=off)";
		InClimaxMin.SetInt(0);
		InClimaxMin.SetIntLimits(0, 1000000);
		InClimaxMin.DisplayOrder = order++;

		InTriggerSize.Name = "Trigger marker size";
		InTriggerSize.SetInt(5);
		InTriggerSize.SetIntLimits(1, 50);
		InTriggerSize.DisplayOrder = order++;

		InTriggerAlert.Name = "Alert on trigger";
		InTriggerAlert.SetYesNo(true);
		InTriggerAlert.DisplayOrder = order++;

		InRelVolumeGate.Name = "Skip bars below volume MA";
		InRelVolumeGate.SetYesNo(false);
		InRelVolumeGate.DisplayOrder = order++;

		InVolumeMALen.Name = "Volume MA length";
		InVolumeMALen.SetInt(50);
		InVolumeMALen.SetIntLimits(2, 500);
		InVolumeMALen.DisplayOrder = order++;

		InMinVolVsMAPct.Name = "Min volume vs MA %";
		InMinVolVsMAPct.SetInt(50);
		InMinVolVsMAPct.SetIntLimits(0, 200);
		InMinVolVsMAPct.DisplayOrder = order++;

		InScaleWithMA.Name = "Scale volume/delta/climax with volume MA";
		InScaleWithMA.SetYesNo(false);
		InScaleWithMA.DisplayOrder = order++;

		InBaselineVolume.Name = "Baseline volume for scaling";
		InBaselineVolume.SetFloat(100);
		InBaselineVolume.SetFloatLimits(1, 1000000);
		InBaselineVolume.DisplayOrder = order++;

		InLogSignals.Name = "Log signals to Message Log";
		InLogSignals.SetYesNo(false);
		InLogSignals.DisplayOrder = order++;

		InVersion.Name = "Do not change (study version)";
		InVersion.SetInt(2);
		InVersion.SetIntLimits(2, 2);
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

	float& armed_px = sc.GetPersistentFloat(1);
	float& climax_val = sc.GetPersistentFloat(2);
	float& fbar_max = sc.GetPersistentFloat(3);
	float& fbar_min = sc.GetPersistentFloat(4);

	if (orion::should_reset_persistents(
			sc.IsFullRecalculation != 0, sc.Index, sc.UpdateStartIndex)) {
		armed_dir = 0;
		armed_bar = -1;
		climax_seen = 0;
		fbar_index = -1;
		triggered_bar = -1;
		vap_truncated_logged = 0;
		armed_px = 0;
		climax_val = 0;
		fbar_max = 0;
		fbar_min = 0;
	}

	const int index = sc.Index;
	const int last_index = sc.ArraySize - 1;
	const int arrow_size = InArrowSize.GetInt() < 1 ? 1 : InArrowSize.GetInt();
	const int trigger_size = InTriggerSize.GetInt() < 1 ? 1 : InTriggerSize.GetInt();
	SetupLong.LineWidth = static_cast<unsigned short>(arrow_size);
	SetupShort.LineWidth = static_cast<unsigned short>(arrow_size);
	TriggerLong.LineWidth = static_cast<unsigned short>(trigger_size);
	TriggerShort.LineWidth = static_cast<unsigned short>(trigger_size);

	if (triggered_bar != index) {
		TriggerLong[index] = 0;
		TriggerShort[index] = 0;
	}
	SetupLong[index] = 0;
	SetupShort[index] = 0;

	SCFloatArray max_delta_arr;
	SCFloatArray min_delta_arr;
	const bool have_max = InMaxDelta.GetStudyID() != 0
		&& sc.GetStudyArrayUsingID(InMaxDelta.GetStudyID(), InMaxDelta.GetSubgraphIndex(), max_delta_arr) != 0;
	const bool have_min = InMinDelta.GetStudyID() != 0
		&& sc.GetStudyArrayUsingID(InMinDelta.GetStudyID(), InMinDelta.GetSubgraphIndex(), min_delta_arr) != 0;

	const int ticks_per_level = InTicksPerLevel.GetInt();
	const double imbalance_ratio = static_cast<double>(InImbalanceRatio.GetInt()) / 100.0;
	const int min_stacked = InMinStacked.GetInt();
	const int swing_bars = InSwingBars.GetInt();
	const double tick_size = sc.TickSize;
	if (tick_size <= 0)
		return;
	const bool bar_closed = sc.GetBarHasClosedStatus(index) == BHCS_BAR_HAS_CLOSED;
	const bool forming = (index == last_index) && !bar_closed;

	float max_delta = DeltaAt(sc, max_delta_arr, index, have_max);
	float min_delta = DeltaAt(sc, min_delta_arr, index, have_min);
	if (!have_max || !have_min) {
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
			if (!have_max)
				max_delta = fbar_max;
			if (!have_min)
				min_delta = fbar_min;
		} else {
			if (!have_max)
				max_delta = bar_d > 0 ? bar_d : 0;
			if (!have_min)
				min_delta = bar_d < 0 ? bar_d : 0;
		}
	}

	const bool is_short_armed = armed_dir < 0;
	if (armed_dir != 0) {
		if (index - armed_bar > InLifetimeBars.GetInt()
			|| orion::extreme_broken(
				sc.High[index], sc.Low[index], armed_px, tick_size,
				InInvalidateTicks.GetInt(), is_short_armed)) {
			armed_dir = 0;
			armed_bar = -1;
			climax_seen = 0;
			climax_val = 0;
		}
	}

	const bool allow_setup = !InSetupOnClose.GetYesNo() || bar_closed;
	const int bar_time = sc.BaseDateTimeIn[index].GetTimeInSeconds();
	const bool session_ok = orion::in_session(
		bar_time, InSessionStart.GetTime(), InSessionEnd.GetTime(),
		InSessionFilter.GetYesNo() != 0);

	const double vol_ma = VolumeMA(sc, index, InVolumeMALen.GetInt());
	const bool vol_ok = orion::volume_gate(
		sc.Volume[index], vol_ma, InMinVolVsMAPct.GetInt(),
		InRelVolumeGate.GetYesNo() != 0);

	const double min_vol = orion::scale_threshold(
		InMinVolPerLevel.GetInt(), vol_ma, InBaselineVolume.GetFloat(),
		InScaleWithMA.GetYesNo() != 0);
	const double delta_thresh = orion::scale_threshold(
		InDeltaThreshold.GetInt(), vol_ma, InBaselineVolume.GetFloat(),
		InScaleWithMA.GetYesNo() != 0);
	const double climax_min = orion::scale_threshold(
		InClimaxMin.GetInt(), vol_ma, InBaselineVolume.GetFloat(),
		InScaleWithMA.GetYesNo() != 0);

	orion::VapLevel vap[orion::kMaxLevels];
	orion::GroupedLevel grouped[orion::kMaxLevels];
	bool vap_truncated = false;
	const int nvap = CollectVap(sc, index, vap, orion::kMaxLevels, &vap_truncated);
	if (vap_truncated && vap_truncated_logged == 0) {
		sc.AddMessageToLog("Orion: volume-at-price truncated at 1024 levels on a bar", 1);
		vap_truncated_logged = 1;
	}
	const double vap_bar_delta = orion::bar_delta_from_vap(vap, nvap);
	const double sc_bar_delta = static_cast<double>(sc.AskVolume[index] - sc.BidVolume[index]);
	const double bar_delta = nvap > 0 ? vap_bar_delta : sc_bar_delta;

	if (allow_setup && session_ok && vol_ok && nvap > 0) {
		double bar_volume = 0;
		for (int i = 0; i < nvap; ++i)
			bar_volume += vap[i].volume();

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
			const double offset = InArrowOffset.GetInt() * tick_size;
			armed_dir = dir;
			armed_bar = index;
			climax_seen = 0;
			climax_val = 0;
			if (is_short) {
				armed_px = sc.High[index];
				SetupShort[index] = static_cast<float>(sc.High[index] + offset);
			} else {
				armed_px = sc.Low[index];
				SetupLong[index] = static_cast<float>(sc.Low[index] - offset);
			}
			if (InSetupAlert.GetYesNo() && index >= last_index - 1 && !sc.IsFullRecalculation) {
				SCString msg;
				msg.Format("ORION SETUP %s", is_short ? "SHORT" : "LONG");
				sc.SetAlert(is_short ? 1 : 2, index, msg);
				if (InLogSignals.GetYesNo())
					sc.AddMessageToLog(msg, 0);
			}
		}
	}

	if (!InEnableTrigger.GetYesNo()
		|| !orion::trigger_bar_ok(index, armed_bar, armed_dir))
		return;

	const bool is_short = armed_dir < 0;
	const float extreme_delta = is_short ? max_delta : min_delta;
	if (!climax_seen) {
		if (!orion::climax_reached(extreme_delta, climax_min, is_short))
			return;
		climax_seen = 1;
		climax_val = extreme_delta;
	} else {
		if (is_short && extreme_delta > climax_val)
			climax_val = extreme_delta;
		if (!is_short && extreme_delta < climax_val)
			climax_val = extreme_delta;
	}

	const float current_delta = static_cast<float>(bar_delta);
	if (!orion::rebound_hit(
			climax_val, current_delta, is_short,
			InReboundMode.GetIndex(),
			InReboundAbs.GetInt(),
			InReboundPct.GetInt()))
		return;

	const double trig_off =
		orion::trigger_offset_ticks(InArrowOffset.GetInt()) * tick_size;
	if (is_short)
		TriggerShort[index] = static_cast<float>(sc.High[index] + trig_off);
	else
		TriggerLong[index] = static_cast<float>(sc.Low[index] - trig_off);
	triggered_bar = index;

	if (InTriggerAlert.GetYesNo() && index >= last_index - 1 && !sc.IsFullRecalculation) {
		SCString msg;
		msg.Format("ORION TRIGGER %s - climax rebound", is_short ? "SHORT" : "LONG");
		sc.SetAlert(3, index, msg);
		if (InLogSignals.GetYesNo())
			sc.AddMessageToLog(msg, 0);
	}

	armed_dir = 0;
	armed_bar = -1;
	climax_seen = 0;
	climax_val = 0;
}

