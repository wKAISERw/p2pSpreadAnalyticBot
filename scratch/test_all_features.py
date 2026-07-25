# scratch/test_all_features.py
import asyncio
import sys
import unittest
from decimal import Decimal

# Add parent directory to path
sys.path.append('.')

from core.engine.risk_engine import CompositeScorer

class TestRiskEngineFeatures(unittest.TestCase):
    def test_composite_scorer_weights_fallback(self):
        # Default weights
        self.assertAlmostEqual(CompositeScorer.W_REGEX + CompositeScorer.W_BEHAVIOR + 
                               CompositeScorer.W_REVIEWS_PCT + CompositeScorer.W_REVIEWS_TEXT + 
                               CompositeScorer.W_LLM + CompositeScorer.W_IDENTITY, 1.0)
        
    def test_composite_scorer_compute(self):
        # Test basic compute logic
        score = CompositeScorer.compute(
            regex_score=50,
            behavior_score=50,
            review_neg_pct=10.0,
            llm_verdict="OK",
            is_twin=False,
            finish_rate=95.0,
            order_count=100,
            review_text_score=0.0,
            review_trend_penalty=0
        )
        self.assertTrue(0 <= score <= 100)
        self.assertEqual(CompositeScorer.to_verdict(score), "WARN" if score >= 20 else "OK")

    def test_clamping_solver(self):
        # Emulate the solver logic from filters.py
        bounds = {
            "W_REGEX": (0.05, 0.45),
            "W_BEHAVIOR": (0.05, 0.35),
            "W_REVIEWS_PCT": (0.05, 0.25),
            "W_REVIEWS_TEXT": (0.05, 0.20),
            "W_LLM": (0.05, 0.35),
            "W_IDENTITY": (0.05, 0.25)
        }
        
        # Test a case where W_REGEX is boosted high and W_REVIEWS_TEXT is boosted high
        # We start with default weights and add some large deltas
        current_w = {
            "W_REGEX": 0.28,
            "W_BEHAVIOR": 0.22,
            "W_REVIEWS_PCT": 0.12,
            "W_REVIEWS_TEXT": 0.08,
            "W_LLM": 0.20,
            "W_IDENTITY": 0.10
        }
        
        delta_weights = {
            "W_REGEX": 0.30,  # massive boost
            "W_BEHAVIOR": 0.0,
            "W_REVIEWS_PCT": 0.0,
            "W_REVIEWS_TEXT": 0.15, # massive boost
            "W_LLM": 0.0,
            "W_IDENTITY": 0.0
        }
        
        raw_new = {k: current_w[k] + delta_weights[k] for k in current_w}
        
        w = raw_new.copy()
        for _ in range(15):
            tot = sum(w.values())
            self.assertTrue(tot > 0)
            w = {k: v / tot for k, v in w.items()}
            
            clamped = {}
            free = {}
            for k, v in w.items():
                low, high = bounds[k]
                if v < low:
                    clamped[k] = low
                elif v > high:
                    clamped[k] = high
                else:
                    free[k] = v
            if not clamped:
                break
            
            clamped_sum = sum(clamped.values())
            free_sum = sum(free.values())
            if free_sum > 0:
                rem = 1.0 - clamped_sum
                w = {}
                for k in clamped:
                    w[k] = clamped[k]
                for k in free:
                    w[k] = free[k] * (rem / free_sum)
            else:
                break
                
        tot = sum(w.values())
        optimized_w = {k: round(v / tot, 4) for k, v in w.items()}
        
        # Assert constraints are satisfied
        for k, val in optimized_w.items():
            low, high = bounds[k]
            self.assertTrue(low - 0.0001 <= val <= high + 0.0001, f"{k} has value {val} out of bounds ({low}, {high})")
            
        # Assert sums to 1.0 approximately
        self.assertAlmostEqual(sum(optimized_w.values()), 1.0, places=3)
        print("Optimized weights verification passed:", optimized_w)

if __name__ == "__main__":
    unittest.main()
