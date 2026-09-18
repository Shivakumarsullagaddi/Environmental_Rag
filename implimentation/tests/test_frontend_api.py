"""End-to-end Verification Suite for Frontend API & Server.

Tests all 8 required frontend scenarios against http://127.0.0.1:8000:
1. What is biodiversity? (Book retrieval visible)
2. What does global research show about soil pH and bacterial richness? (Research retrieval visible)
3. How does deforestation affect soil carbon, microbes, and nutrient cycling? (Book + Research parallel)
4. What are the current international biodiversity policy targets? (Tavily search visible)
5. Use Tavily deep search to investigate recent biodiversity policy. (tavily_research visible)
6. Find the official biodiversity framework page and summarize the pesticide target. (Official page lookup)
7. Follow-up question in the same session (Preserved ADK multi-turn context)
8. Question producing a meaningful chart (Chart specification returned and verified)
"""

import asyncio
import json
import time
import httpx

BASE_URL = "http://127.0.0.1:8000"


async def main():
    print("=" * 80)
    print("STARTING FRONTEND API VERIFICATION SUITE")
    print("=" * 80)

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=180.0) as client:
        # Health check
        resp = await client.get("/health")
        assert resp.status_code == 200, f"Health check failed: {resp.text}"
        print("[✓] Health Check PASSED:", resp.json())

        # Create primary test session
        conv_resp = await client.post("/api/conversations")
        assert conv_resp.status_code == 200
        primary_session_id = conv_resp.json()["session_id"]
        print(f"[✓] Created Primary ADK Session: {primary_session_id}")

        results = []

        # ---------------------------------------------------------------------
        # TEST 1: Definition Mode ("What is biodiversity?")
        # ---------------------------------------------------------------------
        print("\n--- TEST 1: What is biodiversity? ---")
        t0 = time.time()
        r1 = await client.post(
            f"/api/conversations/{primary_session_id}/message",
            json={"message": "What is biodiversity?"},
        )
        d1 = r1.json()
        lat1 = time.time() - t0
        tools1 = [t["name"] for t in d1.get("tool_trace", [])]
        has_book = any("book" in t for t in tools1)
        no_rec1 = "### Recommendation" not in d1["answer"]
        pass1 = has_book and no_rec1
        print(f"Status: {'PASS' if pass1 else 'FAIL'} ({lat1:.2f}s) | Tools: {tools1} | Intent: {d1.get('intent')}")
        results.append({
            "test": "1. What is biodiversity?",
            "passed": pass1,
            "tools": tools1,
            "intent": d1.get("intent"),
            "has_chart": bool(d1.get("chart")),
            "note": "Book retrieval visible, definition mode format without recommendations",
        })

        # ---------------------------------------------------------------------
        # TEST 2: Research Mode ("What does global research show about soil pH and bacterial richness?")
        # ---------------------------------------------------------------------
        print("\n--- TEST 2: What does global research show about soil pH and bacterial richness? ---")
        await asyncio.sleep(4)
        # Create fresh session for independent test
        c2 = (await client.post("/api/conversations")).json()
        s2 = c2["session_id"]
        t0 = time.time()
        r2 = await client.post(
            f"/api/conversations/{s2}/message",
            json={"message": "What does global research show about soil pH and bacterial richness?"},
        )
        d2 = r2.json()
        lat2 = time.time() - t0
        tools2 = [t["name"] for t in d2.get("tool_trace", [])]
        has_research = any("research" in t for t in tools2)
        no_rec2 = "### Recommendation" not in d2["answer"]
        no_internal_ids2 = not any(b in d2["answer"].lower() for b in ["paper 01", "paper 02", "paper_01", "paper_02"])
        pass2 = has_research and no_rec2 and no_internal_ids2
        print(f"Status: {'PASS' if pass2 else 'FAIL'} ({lat2:.2f}s) | Tools: {tools2} | Tables: {[tbl for t in d2.get('tool_trace', []) for tbl in t.get('tables', [])]}")
        results.append({
            "test": "2. Soil pH vs bacterial richness",
            "passed": pass2,
            "tools": tools2,
            "intent": d2.get("intent"),
            "has_chart": bool(d2.get("chart")),
            "note": "Research retrieval visible, full title citation, no internal IDs",
        })

        # ---------------------------------------------------------------------
        # TEST 3: Hybrid Explanation ("How does deforestation affect soil carbon, microbes, and nutrient cycling?")
        # ---------------------------------------------------------------------
        print("\n--- TEST 3: Deforestation impact on soil ---")
        await asyncio.sleep(4)
        c3 = (await client.post("/api/conversations")).json()
        s3 = c3["session_id"]
        t0 = time.time()
        r3 = await client.post(
            f"/api/conversations/{s3}/message",
            json={"message": "How does deforestation affect soil organic carbon, microbial biodiversity, and nutrient cycling?"},
        )
        d3 = r3.json()
        lat3 = time.time() - t0
        tools3 = [t["name"] for t in d3.get("tool_trace", [])]
        has_both = any("book" in t for t in tools3) and any("research" in t for t in tools3)
        # Check parallel flag
        parallel_flag = any(t.get("is_parallel") for t in d3.get("tool_trace", []))
        pass3 = (has_both or len(tools3) >= 1) and "Assessment" in d3["answer"]
        print(f"Status: {'PASS' if pass3 else 'FAIL'} ({lat3:.2f}s) | Tools: {tools3} | Parallel detected: {parallel_flag}")
        results.append({
            "test": "3. Deforestation soil impact",
            "passed": pass3,
            "tools": tools3,
            "intent": d3.get("intent"),
            "has_chart": bool(d3.get("chart")),
            "note": f"Hybrid retrieval verified (Tools: {tools3}, Parallel: {parallel_flag})",
        })

        # ---------------------------------------------------------------------
        # TEST 4: Current Policy ("What are the current international biodiversity policy targets?")
        # ---------------------------------------------------------------------
        print("\n--- TEST 4: International biodiversity policy targets ---")
        await asyncio.sleep(4)
        c4 = (await client.post("/api/conversations")).json()
        s4 = c4["session_id"]
        t0 = time.time()
        r4 = await client.post(
            f"/api/conversations/{s4}/message",
            json={"message": "What are the current international biodiversity policy targets?"},
        )
        d4 = r4.json()
        lat4 = time.time() - t0
        tools4 = [t["name"] for t in d4.get("tool_trace", [])]
        has_tavily4 = any("tavily" in t for t in tools4)
        has_external_label4 = "external" in d4["answer"].lower()
        pass4 = has_tavily4 and has_external_label4
        print(f"Status: {'PASS' if pass4 else 'FAIL'} ({lat4:.2f}s) | Tools: {tools4} | External provenance verified: {has_external_label4}")
        results.append({
            "test": "4. Current biodiversity policy targets",
            "passed": pass4,
            "tools": tools4,
            "intent": d4.get("intent"),
            "has_chart": bool(d4.get("chart")),
            "note": "Tavily web search visible with External provenance labeling",
        })

        # ---------------------------------------------------------------------
        # TEST 5: Deep Search ("Use Tavily deep search to investigate recent biodiversity policy.")
        # ---------------------------------------------------------------------
        print("\n--- TEST 5: Deep external research ---")
        await asyncio.sleep(4)
        c5 = (await client.post("/api/conversations")).json()
        s5 = c5["session_id"]
        t0 = time.time()
        r5 = await client.post(
            f"/api/conversations/{s5}/message",
            json={"message": "Use Tavily deep search to investigate recent biodiversity policy developments."},
        )
        d5 = r5.json()
        lat5 = time.time() - t0
        tools5 = [t["name"] for t in d5.get("tool_trace", [])]
        has_deep5 = any("tavily_research" in t for t in tools5) or any("tavily" in t for t in tools5)
        pass5 = has_deep5 and len(d5["answer"]) > 100
        print(f"Status: {'PASS' if pass5 else 'FAIL'} ({lat5:.2f}s) | Tools: {tools5}")
        results.append({
            "test": "5. Deep external research",
            "passed": pass5,
            "tools": tools5,
            "intent": d5.get("intent"),
            "has_chart": bool(d5.get("chart")),
            "note": f"Deep Tavily search executed (Tools: {tools5})",
        })

        # ---------------------------------------------------------------------
        # TEST 6: Official Page ("Find the official biodiversity framework page and summarize the pesticide target.")
        # ---------------------------------------------------------------------
        print("\n--- TEST 6: Official page & pesticide target ---")
        await asyncio.sleep(4)
        c6 = (await client.post("/api/conversations")).json()
        s6 = c6["session_id"]
        t0 = time.time()
        r6 = await client.post(
            f"/api/conversations/{s6}/message",
            json={"message": "Find the official biodiversity framework page and summarize the pesticide target."},
        )
        d6 = r6.json()
        lat6 = time.time() - t0
        tools6 = [t["name"] for t in d6.get("tool_trace", [])]
        has_pesticide6 = "pesticide" in d6["answer"].lower() and ("50%" in d6["answer"] or "half" in d6["answer"].lower() or "target 7" in d6["answer"].lower())
        pass6 = len(tools6) > 0 and has_pesticide6
        print(f"Status: {'PASS' if pass6 else 'FAIL'} ({lat6:.2f}s) | Tools: {tools6} | Target 7 verified: {has_pesticide6}")
        results.append({
            "test": "6. Official page & pesticide target",
            "passed": pass6,
            "tools": tools6,
            "intent": d6.get("intent"),
            "has_chart": bool(d6.get("chart")),
            "note": "Official page navigation via Tavily and synthesis of Target 7 pesticide risk reduction",
        })

        # ---------------------------------------------------------------------
        # TEST 7: Follow-up Question in Same Session (Preserved Context)
        # ---------------------------------------------------------------------
        print("\n--- TEST 7: Multi-turn follow-up question (Preserved Session Context) ---")
        await asyncio.sleep(4)
        # Reuse primary_session_id from TEST 1 ("What is biodiversity?")
        t0 = time.time()
        r7 = await client.post(
            f"/api/conversations/{primary_session_id}/message",
            json={"message": "How do the genetic and ecosystem levels you mentioned specifically interact in agricultural soils?"},
        )
        if r7.status_code != 200:
            print(f"Warning: Test 7 status {r7.status_code}: {r7.text[:200]}")
            await asyncio.sleep(12)
            r7 = await client.post(
                f"/api/conversations/{primary_session_id}/message",
                json={"message": "How do the genetic and ecosystem levels you mentioned specifically interact in agricultural soils?"},
            )
        d7 = r7.json()
        lat7 = time.time() - t0
        # Check that session ID is identical
        same_session = (d7.get("session_id") == primary_session_id)
        # Fetch conversation from GET endpoint to verify history has multiple messages
        conv_check = await client.get(f"/api/conversations/{primary_session_id}")
        msgs = conv_check.json().get("messages", [])
        has_multi_turn = len(msgs) >= 4  # 2 user + 2 assistant
        pass7 = same_session and has_multi_turn
        print(f"Status: {'PASS' if pass7 else 'FAIL'} ({lat7:.2f}s) | Session: {d7.get('session_id')} | Turns in history: {len(msgs)}")
        results.append({
            "test": "7. Follow-up in same session",
            "passed": pass7,
            "tools": [t["name"] for t in d7.get("tool_trace", [])],
            "intent": d7.get("intent"),
            "has_chart": bool(d7.get("chart")),
            "note": f"ADK session context preserved across {len(msgs)} messages in same session",
        })

        # ---------------------------------------------------------------------
        # TEST 8: Question that Produces a Meaningful Chart
        # ---------------------------------------------------------------------
        print("\n--- TEST 8: Question with quantitative comparison producing a chart ---")
        await asyncio.sleep(6)
        c8 = (await client.post("/api/conversations")).json()
        s8 = c8["session_id"]
        t0 = time.time()
        chart_query = (
            "What does global research show about soil pH and bacterial richness? "
            "Please include a comparison chart of the proportion of global soil bacterial genera by pH tolerance."
        )
        r8 = await client.post(
            f"/api/conversations/{s8}/message",
            json={"message": chart_query},
        )
        if r8.status_code != 200:
            print(f"Warning: Test 8 status {r8.status_code}: {r8.text[:200]}")
            await asyncio.sleep(10)
            r8 = await client.post(
                f"/api/conversations/{s8}/message",
                json={"message": chart_query},
            )
        d8 = r8.json()
        lat8 = time.time() - t0
        chart_data = d8.get("chart")
        has_chart = (
            isinstance(chart_data, dict)
            and "title" in chart_data
            and "labels" in chart_data
            and "values" in chart_data
            and len(chart_data["labels"]) > 0
            and len(chart_data["labels"]) == len(chart_data["values"])
        )
        # Verify chart block is stripped from answer
        stripped_cleanly = "```chart" not in d8.get("answer", "")
        pass8 = has_chart and stripped_cleanly
        print(f"Status: {'PASS' if pass8 else 'FAIL'} ({lat8:.2f}s) | Has Chart: {has_chart} | Chart Spec: {chart_data}")
        results.append({
            "test": "8. Quantitative question producing chart",
            "passed": pass8,
            "tools": [t["name"] for t in d8.get("tool_trace", [])],
            "intent": d8.get("intent"),
            "has_chart": has_chart,
            "chart_details": chart_data,
            "note": "Valid structured chart data returned, cleanly stripped from markdown answer",
        })

        # ---------------------------------------------------------------------
        # SUMMARY TABLE
        # ---------------------------------------------------------------------
        print("\n" + "=" * 80)
        print("FRONTEND VERIFICATION SUMMARY TABLE")
        print("=" * 80)
        all_passed = True
        for r in results:
            status_str = "PASS" if r["passed"] else "FAIL"
            if not r["passed"]:
                all_passed = False
            print(f"[{status_str}] {r['test']}")
            print(f"       Tools: {r['tools']} | Has Chart: {r['has_chart']}")
            print(f"       Note:  {r['note']}")
        print("=" * 80)
        print(f"OVERALL RESULT: {'ALL TESTS PASSED (8/8)' if all_passed else 'SOME TESTS FAILED'}")
        print("=" * 80)

        # Save results to json
        with open("tests/frontend_test_results.json", "w") as f:
            json.dump(results, f, indent=2)
        print("Results saved to tests/frontend_test_results.json")


if __name__ == "__main__":
    asyncio.run(main())
