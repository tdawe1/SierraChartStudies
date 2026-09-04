#include "orion_core.h"

#include <cstdio>
#include <cstdlib>

static int g_failed = 0;

static void expect(bool cond, const char* name) {
	if (cond) {
		std::printf("ok  %s\n", name);
		return;
	}
	std::printf("FAIL  %s\n", name);
	++g_failed;
}

static void expect_int(int got, int want, const char* name) {
	if (got == want) {
		std::printf("ok  %s (%d)\n", name, got);
		return;
	}
	std::printf("FAIL  %s got=%d want=%d\n", name, got, want);
	++g_failed;
}

static void expect_near(double got, double want, double eps, const char* name) {
	if (std::fabs(got - want) <= eps) {
		std::printf("ok  %s (%.4f)\n", name, got);
		return;
	}
	std::printf("FAIL  %s got=%.6f want=%.6f\n", name, got, want);
	++g_failed;
}

int main() {
	expect_int(orion::floor_div(10, 3), 3, "floor_div positive");
	expect_int(orion::floor_div(-1, 4), -1, "floor_div negative");
	expect_int(orion::bucket_of(25, 4), 6, "bucket_of");

	orion::VapLevel vap[6];
	vap[0] = {100, 1, 10};
	vap[1] = {101, 2, 12};
	vap[2] = {102, 8, 2};
	vap[3] = {110, 20, 1};
	vap[4] = {111, 18, 2};
	vap[5] = {112, 15, 3};

	orion::GroupedLevel grouped[orion::kMaxLevels];
	const int ng = orion::group_vap(vap, 6, 1, grouped, orion::kMaxLevels);
	expect_int(ng, 6, "group ticks=1 keeps six levels");

	const int ng2 = orion::group_vap(vap, 3, 2, grouped, orion::kMaxLevels);
	expect_int(ng2, 2, "group ticks=2 merges 100-101 and 102");
	expect_near(grouped[0].ask, 22, 1e-9, "grouped ask 100-101");

	orion::VapLevel low_stack[3];
	low_stack[0] = {50, 1, 12};
	low_stack[1] = {51, 2, 11};
	low_stack[2] = {52, 4, 4};
	orion::GroupedLevel g_low[8];
	const int nlow = orion::group_vap(low_stack, 3, 1, g_low, 8);
	orion::StackResult long_stack = orion::count_stacked(g_low, nlow, false, 8, 3.0, 3);
	expect_int(long_stack.stacked, 2, "long stacked ask 3:1 from low");

	orion::VapLevel high_stack[3];
	high_stack[0] = {90, 4, 4};
	high_stack[1] = {91, 11, 2};
	high_stack[2] = {92, 12, 1};
	orion::GroupedLevel g_high[8];
	const int nhigh = orion::group_vap(high_stack, 3, 1, g_high, 8);
	orion::StackResult short_stack = orion::count_stacked(g_high, nhigh, true, 8, 3.0, 3);
	expect_int(short_stack.stacked, 2, "short stacked bid 3:1 from high");

	orion::VapLevel gapped[3];
	gapped[0] = {50, 1, 12};
	gapped[1] = {51, 2, 11};
	gapped[2] = {60, 1, 20};
	orion::GroupedLevel g_gap[8];
	const int ngap = orion::group_vap(gapped, 3, 1, g_gap, 8);
	orion::StackResult gap_stack = orion::count_stacked(g_gap, ngap, false, 1, 3.0, 20);
	expect_int(gap_stack.stacked, 2, "gap stops stacked walk");

	expect_int(orion::poc_price_ticks(high_stack, 3, true), 92, "poc tie breaks toward high");
	expect_int(orion::poc_price_ticks(high_stack, 3, false), 91, "poc tie breaks toward low");
	expect(orion::poc_isolated_in_wick(92, 90.0, 89.5, 1.0, true), "poc in upper wick");
	expect(!orion::poc_isolated_in_wick(90, 90.0, 89.5, 1.0, true), "poc in body is not isolated");
	expect(orion::poc_in_extreme_range_pct(92, 92.0, 80.0, 1.0, 20.0, true), "poc in top 20%");
	expect(!orion::poc_in_extreme_range_pct(85, 92.0, 80.0, 1.0, 20.0, true), "poc not in top 20%");
	expect(orion::opposite_color_close(10, 9, true), "short wants red close");
	expect(orion::opposite_color_close(10, 11, false), "long wants green close");

	expect(orion::volume_gate(40, 100, 50, true) == false, "gate rejects thin bar");
	expect(orion::volume_gate(60, 100, 50, true), "gate allows 50%+ of MA");
	expect(orion::volume_gate(1, 100, 50, false), "gate off always passes");

	expect_near(orion::scale_threshold(20, 20, 100, true), 4.0, 1e-9, "thin session scales 20/100");
	expect_near(orion::scale_threshold(20, 5, 100, true), 3.0, 1e-9, "scale floor 0.15");
	expect_near(orion::scale_threshold(20, 100, 100, true), 20.0, 1e-9, "baseline session unscaled");
	expect_near(orion::scale_threshold(20, 20, 100, false), 20.0, 1e-9, "scale off keeps absolute");

	expect(orion::climax_reached(25, 20, true), "short climax on +max delta");
	expect(orion::climax_reached(-25, 20, false), "long climax on -min delta");
	expect(!orion::climax_reached(5, 20, true), "short climax below min");

	expect(orion::rebound_hit(40, 30, true, 0, 8, 20), "short abs rebound 10 >= 8");
	expect(!orion::rebound_hit(40, 35, true, 0, 8, 20), "short abs rebound 5 < 8");
	expect(orion::rebound_hit(40, 28, true, 1, 8, 25), "short 30% rebound of 40");
	expect(orion::rebound_hit(-40, -30, false, 0, 8, 20), "long abs rebound");
	expect(orion::rebound_hit(-40, -28, false, 1, 8, 25), "long 30% rebound");

	expect(orion::in_session(10 * 3600, 9 * 3600, 16 * 3600, true), "inside RTH");
	expect(!orion::in_session(3 * 3600, 9 * 3600, 16 * 3600, true), "outside RTH");
	expect(orion::in_session(22 * 3600, 18 * 3600, 2 * 3600, true), "overnight wrap");
	expect(orion::in_session(1, 0, 0, true), "equal start/end means all day");

	expect(orion::extreme_broken(101, 99, 100, 1, 0, true) == false, "0 ticks disables invalidation");
	expect(orion::extreme_broken(105, 99, 100, 1, 4, true), "short invalidates 5 ticks above");
	expect(orion::extreme_broken(101, 95, 100, 1, 4, false), "long invalidates 5 ticks below");

	double highs[6] = {10, 11, 12, 15, 14, 13};
	expect(orion::lookback_extreme(highs, 3, 3, true), "lookback high at bar 3");
	expect(!orion::lookback_extreme(highs, 4, 3, true), "bar 4 is not lookback high");
	double lows[6] = {9, 8, 7, 4, 5, 6};
	expect(orion::lookback_extreme(lows, 3, 3, false), "lookback low at bar 3");
	expect(!orion::lookback_extreme(lows, 2, 3, false), "bar 2 is not lookback low");

	expect(orion::bar_delta_supports(5, 0, false), "long allows +delta at thresh 0");
	expect(!orion::bar_delta_supports(-1, 0, false), "long rejects -delta at thresh 0");
	expect(orion::bar_delta_supports(-12, 8, true), "short needs delta <= -thresh");
	expect(!orion::bar_delta_supports(-3, 8, true), "short rejects small negative delta");

	orion::GroupedLevel scratch[orion::kMaxLevels];
	orion::StackResult ms = orion::count_stacked_multiscale(
		low_stack, 3, 1, false, 8, 3.0, 3, 2, scratch, orion::kMaxLevels);
	expect_int(ms.stacked, 2, "multiscale still finds 2-stack at 1 tick");
	expect_int(ms.scale, 1, "winning scale is 1 tick");
	expect_int(ms.poc_ticks, orion::grouped_poc_ticks(ms.poc_bucket, ms.scale), "grouped poc ticks match scale");

	orion::GroupedLevel g2[8];
	const int ng_poc = orion::group_vap(high_stack, 3, 2, g2, 8);
	const int poc_b = orion::grouped_poc_bucket(g2, ng_poc, true);
	expect_int(orion::grouped_poc_ticks(poc_b, 2), poc_b * 2 + 1, "grouped poc at bucket center");

	expect(orion::bar_delta_filter(-3, 8, true, 1), "magnitude cap allows |delta| < thresh");
	expect(!orion::bar_delta_filter(-12, 8, true, 1), "magnitude cap rejects |delta| > thresh");
	expect(orion::bar_delta_filter(-12, 8, true, 0), "directional still uses sign/min");

	expect_int(orion::pick_setup_direction(true, 3, true, 2, -5), 1, "mutex prefers larger stack");
	expect_int(orion::pick_setup_direction(true, 2, true, 2, -5), -1, "tie uses bar delta sign");
	expect_int(orion::pick_setup_direction(false, 0, true, 2, 9), -1, "short only");
	expect_int(orion::trigger_offset_ticks(4), 7, "trigger offset is arrow+3");

	expect(orion::trigger_bar_ok(11, 10, 1), "trigger allowed on next bar");
	expect(!orion::trigger_bar_ok(10, 10, 1), "no trigger on arm bar");
	expect(!orion::trigger_bar_ok(12, 10, 0), "no trigger when disarmed");
	expect(!orion::trigger_bar_ok(12, -1, 1), "no trigger without arm bar");

	expect(orion::should_reset_persistents(true, 0, 0), "reset at update start 0");
	expect(orion::should_reset_persistents(true, 40, 40), "reset at update start 40");
	expect(!orion::should_reset_persistents(true, 41, 40), "no reset later in recalc");
	expect(!orion::should_reset_persistents(false, 0, 0), "no reset unless full recalc");

	expect_near(orion::bar_delta_from_vap(low_stack, 3), (12-1)+(11-2)+(4-4), 1e-9, "vap bar delta");

	orion::VapLevel many[4];
	many[0] = {1, 1, 1};
	many[1] = {2, 1, 1};
	many[2] = {3, 1, 1};
	many[3] = {4, 1, 1};
	orion::GroupedLevel tiny[2];
	bool trunc = false;
	const int ng_tiny = orion::group_vap(many, 4, 1, tiny, 2, &trunc);
	expect_int(ng_tiny, 2, "group_vap stops at maxdst");
	expect(trunc, "group_vap sets truncated");

	if (g_failed != 0) {
		std::printf("\n%d failed\n", g_failed);
		return 1;
	}
	std::printf("\nall passed\n");
	return 0;
}
