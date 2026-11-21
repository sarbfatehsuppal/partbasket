#include <algorithm>
#include <cstdint>
#include <iostream>
#include <optional>
#include <tuple>
#include <vector>

using Money = std::int64_t;

struct Choice {
    int supplier;
    int offer;
    int units;
    Money cost;
};

struct Plan {
    Money parts = 0;
    Money shipping = 0;
    int supplier_count = 0;
    std::vector<Choice> lines;
};

bool is_better(const Choice& left, const Choice& right) {
    return std::tie(left.cost, left.units, left.offer) <
           std::tie(right.cost, right.units, right.offer);
}

bool is_better(const Plan& left, const Plan& right) {
    return std::make_tuple(left.parts + left.shipping, left.supplier_count) <
           std::make_tuple(right.parts + right.shipping, right.supplier_count);
}

using LineQuotes = std::vector<std::optional<Choice>>;

std::optional<Plan> build_plan(unsigned allowed, const std::vector<Money>& shipping,
                               const std::vector<LineQuotes>& quotes) {
    Plan plan;
    unsigned used = 0;
    for (const auto& row : quotes) {
        std::optional<Choice> best;
        for (unsigned supplier = 0; supplier < shipping.size(); ++supplier) {
            const auto& candidate = row[supplier];
            if ((allowed & (1u << supplier)) && candidate &&
                (!best || is_better(*candidate, *best))) {
                best = candidate;
            }
        }
        if (!best) return std::nullopt;
        plan.parts += best->cost;
        plan.lines.push_back(*best);
        used |= 1u << best->supplier;
    }
    for (unsigned supplier = 0; supplier < shipping.size(); ++supplier) {
        if (used & (1u << supplier)) {
            plan.shipping += shipping[supplier];
            ++plan.supplier_count;
        }
    }
    return plan;
}

void print_plan(const char* name, const std::optional<Plan>& plan) {
    std::cout << name << ' ' << bool(plan);
    if (plan) {
        std::cout << ' ' << plan->parts << ' ' << plan->shipping << ' ' << plan->lines.size();
        for (const auto& line : plan->lines) {
            std::cout << '\n' << line.offer << ' ' << line.units << ' ' << line.cost;
        }
    }
    std::cout << '\n';
}

int main() {
    int suppliers, lines;
    if (!(std::cin >> suppliers >> lines) || suppliers < 1 || suppliers > 8 ||
        lines < 1 || lines > 200) return 2;
    std::vector<Money> shipping(suppliers);
    for (auto& cost : shipping) {
        if (!(std::cin >> cost) || cost < 0 || cost > 1000000000000LL) return 2;
    }
    std::vector<LineQuotes> quotes(lines, LineQuotes(suppliers));
    for (auto& row : quotes) {
        int needed, offers;
        if (!(std::cin >> needed >> offers) || needed < 1 || needed > 1000000 ||
            offers < 0 || offers > 200) return 2;
        for (int i = 0; i < offers; ++i) {
            int supplier, offer, stock, minimum, multiple, tiers;
            if (!(std::cin >> supplier >> offer >> stock >> minimum >> multiple >> tiers) ||
                supplier < 0 || supplier >= suppliers || offer < 0 ||
                stock < 0 || stock > 1000000 || minimum < 1 || minimum > 1000000 ||
                multiple < 1 || multiple > 1000000 || tiers < 1 || tiers > 100) return 2;
            std::vector<std::pair<int, Money>> prices;
            for (int tier = 0; tier < tiers; ++tier) {
                int threshold;
                Money price;
                if (!(std::cin >> threshold >> price) || threshold < 1 ||
                    threshold > 1000000 || price < 1 || price > 1000000000LL) return 2;
                prices.emplace_back(threshold, price);
            }
            std::sort(prices.begin(), prices.end());
            std::optional<Choice> best;
            for (const auto& tier : prices) {
                const int lower = std::max(needed, std::max(minimum, tier.first));
                const int units = ((lower + multiple - 1) / multiple) * multiple;
                if (units > stock) continue;
                const auto applied = std::upper_bound(prices.begin(), prices.end(), units,
                    [](int count, const auto& price) { return count < price.first; });
                const Choice candidate{supplier, offer, units, std::prev(applied)->second * units};
                if (!best || is_better(candidate, *best)) best = candidate;
            }
            if (best && (!row[supplier] || is_better(*best, *row[supplier]))) row[supplier] = best;
        }
    }
    const unsigned all = (1u << suppliers) - 1;
    std::optional<Plan> best, single;
    for (unsigned mask = 1; mask <= all; ++mask) {
        const auto plan = build_plan(mask, shipping, quotes);
        if (!plan) continue;
        if (!best || is_better(*plan, *best)) best = plan;
        if (plan->supplier_count == 1 && (!single || is_better(*plan, *single))) single = plan;
    }
    print_plan("optimized", best);
    print_plan("per_part", build_plan(all, shipping, quotes));
    print_plan("single_store", single);
}
