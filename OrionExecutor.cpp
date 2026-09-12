#include "sierrachart.h"

SCDLLName("Orion Executor")

// Orion Executor: turns Orion trigger subgraphs into managed entries.
//
// Reads TriggerLong/TriggerShort from the Orion study on the same chart
// (GetStudyArrayUsingID) and fires a market entry with attached stop/target
// brackets (bytes: sc.BuyEntry / sc.SellEntry, s_SCNewOrder — see Sierra
// "Automated Trading From an Advanced Custom Study").
//
// Halt contract (Step 6): no entry fires unless ALL hold —
//   (a) our Enabled input is Yes,
//   (b) the PropRiskOverlay Halt state reads 0 at this bar
//       (unwired/failed halt read while InRequireHalt is Yes = halted),
//   (c) the Orion trigger study is wired and readable,
//   (d) GetTradePosition shows flat (v1: one position, no reversal),
//   (e) the order call itself returns accepted (Result > 0). A rejection
//       (e.g. native Global P/L lock, auto-trading disabled) is the
//       native-lock detector: it is logged, never retried on that bar.
// One entry per trigger bar (persistent last-entry index) plus
// sc.AllowOnlyOneTradePerBar.
//
// Backtest parity: the offline twin is strategies.signal_replay over the
// exported Trigger columns — wire Orion's TriggerLong/TriggerShort
// subgraphs into the BacktestExporter's SignalLong/SignalShort inputs,
// export, and run with exec_mode next_open, stop_ticks/target_ticks equal
// to InStopTicks/InTargetTicks. The chart decides; both paths only
// simulate/fire fills.
//
// Sim-first: validate in Trade Simulation Mode before live use. Order
// calls on historical bars are ignored by Sierra (SCT_SKIPPED_FULL_RECALC),
// so backfills and replays cannot emit orders.

namespace {
// Status codes published on Subgraph[0] ("Last action").
const int ST_IDLE = 0;
const int ST_LONG = 1;
const int ST_SHORT = -1;
const int ST_HALTED = 2;
const int ST_HAS_POSITION = 3;
const int ST_ORDER_ERROR = 4;
const int ST_NO_SIGNAL_SOURCE = 5;
}  // namespace

SCSFExport scsf_OrionExecutor(SCStudyInterfaceRef sc) {
    SCSubgraphRef Status = sc.Subgraph[0];

    SCInputRef InEnabled = sc.Input[0];
    SCInputRef InOrionStudyID = sc.Input[1];
    SCInputRef InTrigLongGraph = sc.Input[2];
    SCInputRef InTrigShortGraph = sc.Input[3];
    SCInputRef InHaltStudyID = sc.Input[4];
    SCInputRef InHaltGraph = sc.Input[5];
    SCInputRef InRequireHalt = sc.Input[6];
    SCInputRef InAllowLong = sc.Input[7];
    SCInputRef InAllowShort = sc.Input[8];
    SCInputRef InQty = sc.Input[9];
    SCInputRef InStopTicks = sc.Input[10];
    SCInputRef InTargetTicks = sc.Input[11];

    if (sc.SetDefaults) {
        sc.GraphName = "Orion Executor";
        sc.StudyDescription = "Fires managed market entries from Orion triggers under the PropRiskOverlay halt gate. Sim-first.";
        sc.AutoLoop = 1;
        sc.AllowOnlyOneTradePerBar = 1;
        sc.SupportAttachedOrdersForTrading = 1;

        Status.Name = "Last action (1=long -1=short 2=halted 3=pos 4=err 5=unwired 0=idle)";
        Status.DrawStyle = DRAWSTYLE_IGNORE;

        InEnabled.Name = "Enabled (must be Yes to trade)";
        InEnabled.SetYesNo(0);
        InOrionStudyID.Name = "Orion study ID (0 = unwired, no entries)";
        InOrionStudyID.SetInt(0);
        InTrigLongGraph.Name = "TriggerLong subgraph index";
        InTrigLongGraph.SetInt(2);
        InTrigShortGraph.Name = "TriggerShort subgraph index";
        InTrigShortGraph.SetInt(3);
        InHaltStudyID.Name = "Halt study ID (PropRiskOverlay; 0 = unwired)";
        InHaltStudyID.SetInt(0);
        InHaltGraph.Name = "Halt subgraph index";
        InHaltGraph.SetInt(1);
        InRequireHalt.Name = "Require halt gate (unwired halt = halted)";
        InRequireHalt.SetYesNo(1);
        InAllowLong.Name = "Allow long entries";
        InAllowLong.SetYesNo(1);
        InAllowShort.Name = "Allow short entries";
        InAllowShort.SetYesNo(1);
        InQty.Name = "Contracts per entry";
        InQty.SetInt(1);
        InQty.SetIntLimits(1, 100);
        InStopTicks.Name = "Attached stop ticks (0 = none)";
        InStopTicks.SetInt(0);
        InStopTicks.SetIntLimits(0, 1000);
        InTargetTicks.Name = "Attached target ticks (0 = none)";
        InTargetTicks.SetInt(0);
        InTargetTicks.SetIntLimits(0, 1000);
        return;
    }

    int& last_entry_bar = sc.GetPersistentInt(0);

    if (InEnabled.GetYesNo() == 0) {
        Status[sc.Index] = static_cast<float>(ST_IDLE);
        return;
    }

    // Act on closed bars only; the forming bar can neither arm a trigger
    // nor accept a reliable halt read.
    if (sc.GetBarHasClosedStatus(sc.Index) != BHCS_BAR_HAS_CLOSED) {
        Status[sc.Index] = static_cast<float>(ST_IDLE);
        return;
    }

    // (b) Halt gate first: fail-closed.
    const int halt_id = InHaltStudyID.GetInt();
    if (InRequireHalt.GetYesNo() != 0) {
        SCFloatArray halt_arr;
        const bool halt_ok = halt_id > 0
            && sc.GetStudyArrayUsingID(halt_id, InHaltGraph.GetInt(), halt_arr) != 0;
        if (!halt_ok || halt_arr[sc.Index] != 0.0f) {
            Status[sc.Index] = static_cast<float>(ST_HALTED);
            return;
        }
    }

    // (c) Trigger source must be wired and readable.
    const int orion_id = InOrionStudyID.GetInt();
    SCFloatArray trig_long, trig_short;
    const bool sig_ok = orion_id > 0
        && sc.GetStudyArrayUsingID(orion_id, InTrigLongGraph.GetInt(), trig_long) != 0
        && sc.GetStudyArrayUsingID(orion_id, InTrigShortGraph.GetInt(), trig_short) != 0;
    if (!sig_ok) {
        Status[sc.Index] = static_cast<float>(ST_NO_SIGNAL_SOURCE);
        return;
    }

    int direction = 0;
    if (InAllowLong.GetYesNo() != 0 && trig_long[sc.Index] != 0.0f)
        direction = 1;  // long wins ties (mirrors signal_replay/engine)
    else if (InAllowShort.GetYesNo() != 0 && trig_short[sc.Index] != 0.0f)
        direction = -1;
    if (direction == 0) {
        Status[sc.Index] = static_cast<float>(ST_IDLE);
        return;
    }
    if (last_entry_bar == sc.Index) {
        Status[sc.Index] = static_cast<float>(ST_IDLE);
        return;
    }

    // (d) One position at a time, no reversal in v1.
    s_SCPositionData pos;
    if (sc.GetTradePosition(pos) != 0 && pos.PositionQuantity != 0.0) {
        Status[sc.Index] = static_cast<float>(ST_HAS_POSITION);
        return;
    }

    // (e) Fire. Brackets derive from the trigger bar's close so the chart
    // entry matches the backtest's stop_ticks/target_ticks inputs.
    const double tick = sc.TickSize > 0.0 ? sc.TickSize : 0.25;
    const double close = sc.Close[sc.Index];
    s_SCNewOrder order;
    order.OrderQuantity = InQty.GetInt() > 0 ? InQty.GetInt() : 1;
    order.OrderType = SCT_MARKET;
    order.TimeInForce = SCT_TIF_DAY;
    const int stop_ticks = InStopTicks.GetInt();
    const int target_ticks = InTargetTicks.GetInt();
    if (stop_ticks > 0)
        order.Stop1Price = sc.RoundToTickSize(close - direction * stop_ticks * tick, tick);
    if (target_ticks > 0)
        order.Target1Price = sc.RoundToTickSize(close + direction * target_ticks * tick, tick);
    order.TextTag = "orion-exec";

    const int result = direction > 0 ? sc.BuyEntry(order) : sc.SellEntry(order);
    if (result > 0) {
        last_entry_bar = sc.Index;
        Status[sc.Index] = static_cast<float>(direction > 0 ? ST_LONG : ST_SHORT);
    } else {
        Status[sc.Index] = static_cast<float>(ST_ORDER_ERROR);
        if (sc.Index == sc.ArraySize - 1)
            sc.AddMessageToLog(sc.GetTradingErrorTextMessage(result), 1);
    }
}
