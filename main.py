THRESHOLDS = {
    "proportion_to_revenue": 0.20,      # 20%
    "min_profit_margin": 15,             # 15%
    "min_contract_years": 2,
    "max_execution_risk": 3,             # < 3
    "min_credit_rating": ["AAA", "AA+", "AA", "AA-", "A+", "A", "A-", "BBB+", "BBB", "BBB-"],
    "min_upfront_payment": 40,           # 40%
    "min_market_share_increase": 5,      # 5%
    "min_cashflow_improvement": 10,      # 10%
    "min_competitor_gap": 0.10           # 10%
}


import json

def analyze_order(data, thresholds):
    results = {}

    company = data["company"]
    order = data["order"]

    # 1. Proportion to Revenue
    proportion = order["order_value_cr"] / company["annual_revenue_cr"]
    results["Proportion to Revenue"] = proportion >= thresholds["proportion_to_revenue"]

    # 2. Profit Margin
    results["Profit Margin"] = order["profit_margin_percent"] >= thresholds["min_profit_margin"]

    # 3. Order Recurrence
    results["Order Recurrence"] = order["contract_duration_years"] >= thresholds["min_contract_years"]

    # 4. Execution Risk
    results["Execution Risk"] = order["execution_risk_score"] < thresholds["max_execution_risk"]

    # 5. Customer Creditworthiness
    results["Customer Creditworthiness"] = (
        order["customer_credit_rating"] in thresholds["min_credit_rating"]
    )

    # 6. Revenue Recognition Timeline
    results["Revenue Recognition"] = (
        order["upfront_payment_percent"] >= thresholds["min_upfront_payment"]
    )

    # 7. Strategic Fit
    results["Strategic Fit"] = order["strategic_fit"]

    # 8. Industry Leadership
    results["Industry Leadership"] = (
        order["expected_market_share_increase_percent"] >= thresholds["min_market_share_increase"]
    )

    # 9. Cash Flow Impact
    results["Cash Flow Impact"] = (
        order["expected_cashflow_improvement_percent"] >= thresholds["min_cashflow_improvement"]
    )

    # 10. Order Size vs Competitors
    competitor_gap = (
        order["order_value_cr"] - order["competitor_average_order_value_cr"]
    ) / order["competitor_average_order_value_cr"]

    results["Order Size vs Competitors"] = competitor_gap >= thresholds["min_competitor_gap"]

    # Final verdict
    passed = sum(results.values())
    total = len(results)

    verdict = "STRONG POSITIVE" if passed >= 8 else \
              "MODERATE POSITIVE" if passed >= 6 else \
              "WEAK / AVOID"

    return results, passed, total, verdict


# ---- Run analysis ----
if __name__ == "__main__":
    with open("sample.json") as f:
        data = json.load(f)

    results, passed, totaLLM_MODEL l, verdict = analyze_order(data, THRESHOLDS)

    print("Evaluation Results:")
    for k, v in results.items():
        print(f"- {k}: {'PASS' if v else 'FAIL'}")

    print(f"\nScore: {passed}/{total}")
    print(f"Investment Verdict: {verdict}")



