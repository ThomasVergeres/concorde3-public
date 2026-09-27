package core

import (
	"fmt"
	"math"
	"testing"
	"time"
)

// Positive weights have no minimum. A tiny share must not wrap a fairness
// threshold into an immediate entitlement ahead of the protected broad share.
func TestTinyAttentionShareDoesNotInvertPriority(t *testing.T) {
	for _, weight := range []float64{1e-20, math.SmallestNonzeroFloat64} {
		t.Run(fmt.Sprintf("weight=%g", weight), func(t *testing.T) {
			s := fixture(t)
			if err := s.Update("operator", "allocation", "retain negligible exploratory attention", func(st *State) error {
				fund(st, "purpose", .5, "ready")
				fund(st, "tiny", weight, "ready")
				return nil
			}); err != nil {
				t.Fatal(err)
			}
			running(t, s)
			for j := 0; j < 3; j++ {
				// Exercise persisted counters, normal admission, distinct
				// rectification/writeback and a conversation-free reopen.
				now := s.Now
				s = Open(s.Dir)
				s.Now = now
				a, packet, err := s.Admit()
				if err != nil {
					t.Fatal(err)
				}
				if a.Pursuit != "purpose" {
					t.Fatalf("tiny weight %g displaced broad attention: %s (%s)", weight, a.Pursuit, packet.Reason)
				}
				complete(t, s, a, "continue")
			}
		})
	}
}

func TestDocumentedFixedPortfolioWaitingBound(t *testing.T) {
	for _, weights := range [][]float64{{.5, .5}, {.2, .7, .1}, {.2, .79, .01}, {.4, .15, .15, .15, .15}} {
		for _, flood := range []bool{false, true} {
			t.Run(fmt.Sprintf("weights=%v/flood=%v", weights, flood), func(t *testing.T) {
				st := NewState("bounded attention")
				i := st.Items["purpose"]
				i.Attention = nil
				st.Items[i.ID] = i
				now := time.Date(2026, 9, 10, 0, 0, 0, 0, time.UTC)
				total := 0.0
				for j, weight := range weights {
					id := fmt.Sprintf("p.%d", j)
					fund(&st, id, weight, "ready")
					total += weight
					if flood && j != 0 {
						st.Timers[id] = Timer{ID: id, Pursuit: id, Due: now, Active: true}
					}
				}
				st.Config.Reconsideration = "p.0"
				last := map[string]int{}
				for turn := 1; turn <= 2000; turn++ {
					p, _, err := selectPursuit(&st, now)
					if err != nil {
						t.Fatal(err)
					}
					st.Starts++
					last[p.ID] = turn
					for id, pursuit := range st.Portfolio() {
						bound := int(math.Ceil(2*total/pursuit.Share)) + len(weights) - 1
						if turn-last[id] >= bound {
							t.Fatalf("%s unserved for %d admissions, bound=%d", id, turn-last[id], bound)
						}
					}
				}
				if len(last) != len(weights) {
					t.Fatal("missing service", last)
				}
			})
		}
	}
}
