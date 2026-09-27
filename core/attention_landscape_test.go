package core

import (
	"encoding/json"
	"fmt"
	"testing"
	"time"
)

func TestWorkAndRectificationExposeFundedAttentionLandscape(t *testing.T) {
	st := NewState("Sustain the undertaking")
	now := time.Now().UTC()
	for _, phase := range []string{"work", "rectification"} {
		a := Activation{ID: "act.landscape", Pursuit: "purpose", Phase: phase, Started: now, Deadline: now.Add(time.Minute)}
		var packet map[string]json.RawMessage
		if err := json.Unmarshal(Marshal(Assemble(st, a)), &packet); err != nil {
			t.Fatal(err)
		}
		var view struct {
			FundedCount int       `json:"funded_count"`
			Intentions  []Pursuit `json:"intentions"`
			Omitted     int       `json:"omitted"`
		}
		if err := json.Unmarshal(packet["attention_landscape"], &view); err != nil {
			t.Fatalf("%s: missing attention landscape: %v", phase, err)
		}
		if view.FundedCount != 1 || len(view.Intentions) != 1 || view.Intentions[0].ID != "purpose" || view.Omitted != 0 {
			t.Fatalf("%s: sole funded intention obscured: %+v", phase, view)
		}
	}
}

func TestAttentionLandscapeBoundsLargePortfolioWithoutHidingCount(t *testing.T) {
	st := NewState("Sustain the undertaking")
	purpose := st.Items["purpose"]
	purpose.Attention.Weight = .2
	st.Items["purpose"] = purpose
	for n := 0; n < 10; n++ {
		id := fmt.Sprintf("branch-%02d", n)
		st.Items[id] = Item{ID: id, Node: "undertaking", Kind: "intention", Text: "Distinct work",
			Status: "active", Attention: &Attention{Weight: .08, EffortState: "waiting"}}
	}
	now := time.Now().UTC()
	p := Assemble(st, Activation{ID: "act.landscape", Pursuit: "purpose", Phase: "work", Started: now, Deadline: now.Add(time.Minute)})
	if p.Landscape == nil || p.Landscape.FundedCount != 11 || len(p.Landscape.Intentions) != 8 || p.Landscape.Omitted != 3 {
		t.Fatalf("bounded landscape missing true portfolio extent: %+v", p.Landscape)
	}
	if len(Marshal(p)) > st.Config.ContextBytes || p.Landscape.Intentions[0].ID != "purpose" {
		t.Fatal("landscape exceeded packet bound or hid whole-self allocation")
	}
}
