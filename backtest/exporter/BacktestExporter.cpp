#include "sierrachart.h"
#include <cmath>

SCDLLName("Backtest Exporter")

// BacktestExporter: dump bars + study signals to CSV for the headless
// backtester in backtest/ (bt.py). Copy this file into ACS_Source and
// Remote Build it alongside your studies.
//
// What it writes (one row per closed bar, appended once):
//   DateTime,Open,High,Low,Close,Volume,BidVolume,AskVolume,
//   MaxDelta,MinDelta,SignalLong,SignalShort,ATR14,RelVol50,
//   BidClose,AskClose
// ATR14 is the mean true range over the last 14 closed bars (fewer at
// the left edge); RelVol50 is bar volume over its 50-bar mean (1.0 when
// dry). Both use bars at or before the row: no lookahead.
// BidClose/AskClose are the bar's last bid/ask (sc.Bid/sc.Ask). They
// equal the recorded market bid/ask only with Intraday Data Storage
// Time Unit = 1 Tick (else Sierra replays estimated prices; see Trade
// Simulation Method 1 vs 2). Headless consumers treat them as context,
// never as fill prices.
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
    int need_header = (sc.Index == 0 && !doAppend);
    if (sc.Index == 0 && doAppend)
    {
        // Fresh recalc in append mode: write the header only when the
        // file is missing or empty (no-header append quirk fixed here
        // instead of by prepending downstream).
        f = fopen(InPath.GetString(), "r");
        if (f != nullptr)
        {
            fseek(f, 0, SEEK_END);
            need_header = (ftell(f) == 0);
            fclose(f);
            f = nullptr;
        }
        else
        {
            need_header = 1;
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
    if (need_header)
    {
        fprintf(f, "DateTime,Open,High,Low,Close,Volume,BidVolume,AskVolume,"
                   "MaxDelta,MinDelta,SignalLong,SignalShort,ATR14,RelVol50,"
                   "BidClose,AskClose\n");
    }

    for (int i = sc.Index; i <= lastClosed; ++i)
    {
        SCString dt = sc.DateTimeToString(sc.BaseDateTimeIn[i], FLAG_DT_COMPLETE_DATETIME);
        const double bid = sc.BidVolume[i];
        const double ask = sc.AskVolume[i];
        const double mx = haveMax ? (double)maxArr[i] : (ask - bid);
        const double mn = haveMin ? (double)minArr[i] : (ask - bid);
        const int longSig = (haveLong && longArr[i] != 0.0f) ? 1 : 0;
        const int shortSig = (haveShort && shortArr[i] != 0.0f) ? 1 : 0;
        double tr_sum = 0.0;
        int tr_n = 0;
        for (int k = i - 13; k <= i; ++k)
        {
            if (k < 0) continue;
            const double pc = k > 0 ? sc.Close[k - 1] : sc.Close[k];
            double tr = sc.High[k] - sc.Low[k];
            const double ha = fabs(sc.High[k] - pc);
            const double lb = fabs(sc.Low[k] - pc);
            if (ha > tr) tr = ha;
            if (lb > tr) tr = lb;
            tr_sum += tr;
            ++tr_n;
        }
        const double atr14 = tr_n > 0 ? tr_sum / tr_n : 0.0;
        double v_sum = 0.0;
        int v_n = 0;
        for (int k = i - 49; k <= i; ++k)
        {
            if (k < 0) continue;
            v_sum += (double)sc.Volume[k];
            ++v_n;
        }
        const double v_avg = v_n > 0 ? v_sum / v_n : 0.0;
        const double relvol50 = v_avg > 0.0 ? (double)sc.Volume[i] / v_avg : 1.0;
        fprintf(f, "%s,%.6f,%.6f,%.6f,%.6f,%.0f,%.0f,%.0f,%.0f,%.0f,%d,%d,%.6f,%.6f,%.6f,%.6f\n",
                dt.GetChars(), sc.Open[i], sc.High[i], sc.Low[i], sc.Close[i],
                (double)sc.Volume[i], bid, ask, mx, mn, longSig, shortSig,
                atr14, relvol50, (double)sc.Bid[i], (double)sc.Ask[i]);
    }
    fclose(f);
}
