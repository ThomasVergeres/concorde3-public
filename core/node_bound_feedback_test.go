package core

import (
	"strings"
	"testing"
)

func TestNodeBoundFeedbackExplainsMetadataAndPreservesAtomicRecovery(t *testing.T) {
	s := fixture(t)
	running(t, s)
	a, _, err := s.Admit()
	if err != nil {
		t.Fatal(err)
	}
	before, _ := s.Read()
	reason := strings.Repeat("Retain the observed source and why it changes this belief. ", 25)
	batch := Batch{Nodes: []NodeChange{{Node: Node{ID: "evidence", Title: "Evidence", Status: "active"}}}}
	for _, id := range []string{"claim-a", "claim-b", "claim-c"} {
		i := itemFixture(id, "One short observation.")
		i.Node = "evidence"
		i.Sources = []Source{{Ref: "fixture:original", ObservedAt: s.Now()}}
		batch.Items = append(batch.Items, ItemChange{Item: i})
	}
	err = s.Mutate(a.ID, reason, batch)
	if err == nil {
		t.Fatal("oversized node accepted")
	}
	for _, text := range []string{"over_by=", "largest_items=", "reason_bytes=", "sources_bytes=", "claim-a", "claim-b"} {
		if !strings.Contains(err.Error(), text) {
			t.Fatalf("missing actionable bound detail %q: %v", text, err)
		}
	}
	after, _ := s.Read()
	if after.Seq != before.Seq || after.Nodes["evidence"].ID != "" {
		t.Fatal("rejected write mutated state")
	}
	// One legitimate repair, not a required strategy: explicitly separate the
	// contexts while keeping each original source and revision reason intact.
	batch.Nodes = nil
	for index := range batch.Items {
		id := batch.Items[index].Item.ID + ".context"
		batch.Items[index].Item.Node = id
		batch.Nodes = append(batch.Nodes, NodeChange{Node: Node{ID: id, Title: "Evidence", Status: "active"}})
	}
	if err = s.Mutate(a.ID, reason, batch); err != nil {
		t.Fatal(err)
	}
	complete(t, s, a, "continue")
	reopened, err := Open(s.Dir).Read()
	if err != nil {
		t.Fatal(err)
	}
	for _, change := range batch.Items {
		i := reopened.Items[change.Item.ID]
		if i.Reason != reason || i.Actor != a.ID || i.Sources[0] != change.Item.Sources[0] {
			t.Fatal("repair discarded provenance")
		}
	}
	if _, _, err := Open(s.Dir).Admit(); err != nil {
		t.Fatal(err)
	}
}

func TestNodeBoundFeedbackFitsSmallResponseWithLongestIdentifiers(t *testing.T) {
	s := fixture(t)
	state, _ := s.Read()
	id := strings.Repeat("n", 160)
	state.Nodes[id] = Node{ID: id, Title: "Evidence", Status: "active", Revision: 1, Reason: "created"}
	for _, prefix := range []string{"a", "b", "c"} {
		i := itemFixture(prefix+strings.Repeat("x", 159), strings.Repeat("text", 500))
		i.Node, i.Revision, i.Reason = id, 1, "retained"
		state.Items[i.ID] = i
	}
	err := Validate(state)
	if err == nil || !strings.Contains(err.Error(), "largest_items=") {
		t.Fatal("missing bounded diagnostic", err)
	}
	// Include ordinary JSON error-envelope overhead, not only the raw string.
	if len(Marshal(map[string]any{"content": []any{map[string]any{"type": "text", "text": err.Error()}}, "isError": true})) > 1024 {
		t.Fatal("diagnostic exceeds smallest response budget")
	}
}
