#include "sierrachart.h"

SCDLLName("Backtest Exporter")

// BacktestExporter: dump bars + study signals to CSV for the headless
// backtester in backtest/ (bt.py). Copy this file into ACS_Source and
// Remote Build it alongside your studies.
//
// What it writes (one row per closed bar, appended once):
//   DateTime,Open,High,Low,Close,Volume,BidVolume,AskVolume,
//   MaxDelta,MinDelta,SignalLong,SignalShort
//
// Wiring:
//   [Signal] Study + Subgraph to export, twice (long leg, short leg).
//     Point the subgraph at any trigger/setup output -- e.g. Orion's
//     "Trigger Long" / "Trigger Short". A nonzero subgraph value = 1.
//   [Delta] Optional Numbers Bars Max/Min delta subgraphs. Leave Study ID
//     at 0 to export AskVolume-BidVolume as both.
// Usage: add to the chart, set the file path + inputs, Recalculate, then
//   python3 backtest/bt.py run --data <file> --params backtest/params.replay.json

SCSFExport scsf_BacktestExporter(SCStudyInterfaceRef sc)
{
    SCInputRef InPath = sc.Input[0];
    SCInputRef InLongSrc = sc.Input[1];
    SCInputRef InShortSrc = sc.Input[2];
    SCInputRef InMaxDelta = sc.Input[3];
    SCInputRef InMinDelta = sc.Input[4];
    SCInputRef InAppend = sc.Input[5];

    if (sc.SetDefaults)
    {
        sc.GraphName = "Backtest Exporter";
        sc.GraphRegion = 0;
        sc.AutoLoop = 0;
        sc.FreeDLL = 0;

        InPath.Name = "Output CSV path";
        InPath.SetString("C:\\SierraChart\\Data\\bt_export.csv");

        InLongSrc.Name = "[Signal] Long study subgraph";
        InLongSrc.SetStudySubgraphValues(0, 0);

        InShortSrc.Name = "[Signal] Short study subgraph";
        InShortSrc.SetStudySubgraphValues(0, 0);

        InMaxDelta.Name = "[Delta] Max delta subgraph (0=off)";
        InMaxDelta.SetStudySubgraphValues(0, 0);

        InMinDelta.Name = "[Delta] Min delta subgraph (0=off)";
        InMinDelta.SetStudySubgraphValues(0, 0);

        InAppend.Name = "Append (No = overwrite)";
        InAppend.SetYesNo(1);
        return;
    }

    const int n = sc.ArraySize;
    if (n < 2)
        return;

    SCFloatArray longArr;
    SCFloatArray shortArr;
    SCFloatArray maxArr;
    SCFloatArray minArr;
    const bool haveLong = InLongSrc.GetStudyID() != 0 &&
        sc.GetStudyArrayUsingID(InLongSrc.GetStudyID(), InLongSrc.GetSubgraphIndex(), longArr) != 0;
    const bool haveShort = InShortSrc.GetStudyID() != 0 &&
        sc.GetStudyArrayUsingID(InShortSrc.GetStudyID(), InShortSrc.GetSubgraphIndex(), shortArr) != 0;
    const bool haveMax = InMaxDelta.GetStudyID() != 0 &&
        sc.GetStudyArrayUsingID(InMaxDelta.GetStudyID(), InMaxDelta.GetSubgraphIndex(), maxArr) != 0;
    const bool haveMin = InMinDelta.GetStudyID() != 0 &&
        sc.GetStudyArrayUsingID(InMinDelta.GetStudyID(), InMinDelta.GetSubgraphIndex(), minArr) != 0;

    // sc.Index is the starting index for this call chunk; with AutoLoop=0
    // we get one call, so walk every closed bar here.
    const int lastClosed = n - 2; // skip the forming bar
    if (lastClosed < 0 || sc.Index > lastClosed)
        return;

    FILE* f = nullptr;
    const int doAppend = InAppend.GetYesNo();
    if (sc.Index == 0 && doAppend)
    {
        // Fresh recalc: check whether the file already has a header.
        f = fopen(InPath.GetString(), "r");
        if (f != nullptr)
        {
            fclose(f);
            f = nullptr;
        }
    }
    const char* mode = (sc.Index == 0 && !doAppend) ? "w" : "a";
    f = fopen(InPath.GetString(), mode);
    if (f == nullptr)
    {
        SCString msg;
        msg.Format("BacktestExporter: cannot open %s", InPath.GetString());
        sc.AddMessageToLog(msg, 1);
        return;
    }
    if (sc.Index == 0 && !doAppend)
    {
        fprintf(f, "DateTime,Open,High,Low,Close,Volume,BidVolume,AskVolume,"
                   "MaxDelta,MinDelta,SignalLong,SignalShort\n");
    }

    for (int i = sc.Index; i <= lastClosed; ++i)
    {
        char dt[32] = {0};
        sc.DateTimeToString(sc.BaseDateTimeIn[i], dt, (int)sizeof(dt));
        const double bid = sc.BidVolume[i];
        const double ask = sc.AskVolume[i];
        const double mx = haveMax ? (double)maxArr[i] : (ask - bid);
        const double mn = haveMin ? (double)minArr[i] : (ask - bid);
        const int longSig = (haveLong && longArr[i] != 0.0f) ? 1 : 0;
        const int shortSig = (haveShort && shortArr[i] != 0.0f) ? 1 : 0;
        fprintf(f, "%s,%.6f,%.6f,%.6f,%.6f,%.0f,%.0f,%.0f,%.0f,%.0f,%d,%d\n",
                dt, sc.Open[i], sc.High[i], sc.Low[i], sc.Close[i],
                (double)sc.Volume[i], bid, ask, mx, mn, longSig, shortSig);
    }
    fclose(f);
}
