package core

import (
	"context"
	"encoding/json"
	"os"
	"strings"
	"testing"
)

// This subprocess has a content-specific indexing failure while query encoding
// still works. It is transport machinery, not evidence of semantic intelligence.
func TestContextEmbeddingFailureHelper(t *testing.T) {
	if os.Args[len(os.Args)-1] != "context-embedding-failure-fixture" {
		return
	}
	var q struct {
		Texts []string `json:"texts"`
	}
	if err := json.NewDecoder(os.Stdin).Decode(&q); err != nil {
		os.Exit(2)
	}
	vectors := [][]float64{}
	for _, text := range q.Texts {
		if strings.HasPrefix(text, "Unavailable index input\n") {
			os.Exit(3)
		}
		if strings.Contains(text, "archives") || strings.Contains(text, "historical retention") {
			vectors = append(vectors, []float64{1, 0})
		} else {
			vectors = append(vectors, []float64{0, 1})
		}
	}
	if err := json.NewEncoder(os.Stdout).Encode(map[string]any{"vectors": vectors}); err != nil {
		os.Exit(2)
	}
	os.Exit(0)
}

func TestContextSemanticRefreshFailureAcrossRestart(t *testing.T) {
	s, changedNode, changedItem := semanticTitleFixture(t)
	if err := s.Update("operator", "config", "qualify partial-index reporting", func(st *State) error {
		st.Config.EmbeddingCommand = []string{os.Args[0], "-test.run=TestContextEmbeddingFailureHelper", "context-embedding-failure-fixture"}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	healthy := itemFixture("healthy-evidence", "Separate corroboration")
	healthy.Node = "healthy-context"
	if err := s.Mutate("operator", "retain independent healthy semantic coverage", Batch{
		Nodes: []NodeChange{{Node: Node{ID: healthy.Node, Title: "historical retention", Status: "active"}}},
		Items: []ItemChange{{Item: healthy}},
	}); err != nil {
		t.Fatal(err)
	}
	running(t, s)
	a, _, err := s.Admit()
	if err != nil {
		t.Fatal(err)
	}
	check := func(store *Store, actor string, failed bool) {
		t.Helper()
		p, err := store.RefreshContext(context.Background(), actor, "archives")
		if err != nil {
			t.Fatal(err)
		}
		if strings.Contains(p.Semantic, "index refresh failed") != failed {
			t.Fatalf("refresh failure=%v lost/misreported in %s: %q", failed, p.Phase, p.Semantic)
		}
		if !strings.Contains(p.Semantic, "available: revision-checked") {
			t.Fatalf("healthy query/index coverage misreported: %q", p.Semantic)
		}
		found := map[string]bool{}
		for _, candidate := range p.Items {
			found[candidate.Item.ID] = true
		}
		if !found[healthy.ID] || found[changedItem.ID] == failed {
			t.Fatalf("healthy candidate lost or stale semantic candidate admitted: failed=%v, items=%v", failed, found)
		}
	}
	check(s, a.ID, false)
	changedNode.Title = "Unavailable index input"
	if err := s.Mutate(a.ID, "new title exposes encoder failure", Batch{
		Nodes: []NodeChange{{ExpectedRevision: 1, Node: changedNode}},
	}); err != nil {
		t.Fatal(err)
	}
	check(s, a.ID, true)
	if err := s.BeginRectification(a.ID, "retain discovery despite degraded search", "synthetic-session"); err != nil {
		t.Fatal(err)
	}
	check(s, a.ID, true)
	complete(t, s, a, "continue")
	restarted := Open(s.Dir)
	restarted.Now = s.Now
	next, _, err := restarted.Admit()
	if err != nil {
		t.Fatal(err)
	}
	check(restarted, next.ID, true)
	changedNode.Title = "historical retention"
	if err := restarted.Mutate(next.ID, "supported input restores indexing", Batch{
		Nodes: []NodeChange{{ExpectedRevision: 2, Node: changedNode}},
	}); err != nil {
		t.Fatal(err)
	}
	check(restarted, next.ID, false)
	complete(t, restarted, next, "wait")
}
