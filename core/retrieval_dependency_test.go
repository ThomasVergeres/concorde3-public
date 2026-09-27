package core

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"testing"
)

// The fixture encoder is deliberately synthetic. These tests qualify cache
// dependencies and lifecycle, not the quality of an embedding model.
func semanticTitleFixture(t *testing.T) (*Store, Node, Item) {
	t.Helper()
	s := Open(t.TempDir())
	// Avoid incidental lexical matches (the default purpose includes "a").
	// This fixture isolates semantic-cache relevance, not overall retrieval rank.
	if err := s.Init("Sustain operations"); err != nil {
		t.Fatal(err)
	}
	if err := s.Update("operator", "config", "synthetic semantic dependency fixture", func(st *State) error {
		st.Config.EmbeddingCommand = []string{os.Args[0], "-test.run=TestEmbeddingHelper", "embedding-fixture"}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	n := Node{ID: "remote-context", Title: "historical retention", Status: "active"}
	i := itemFixture("remote-evidence", "Independent verification")
	i.Node = n.ID
	if err := s.Mutate("operator", "separate unlinked context", Batch{
		Nodes: []NodeChange{{Node: n}}, Items: []ItemChange{{Item: i}},
	}); err != nil {
		t.Fatal(err)
	}
	return s, n, i
}

func TestSemanticNodeTitleInvalidation(t *testing.T) {
	for _, rebuild := range []bool{false, true} {
		name := "reject_stale"
		if rebuild {
			name = "rebuild_changed_input"
		}
		t.Run(name, func(t *testing.T) {
			s, n, i := semanticTitleFixture(t)
			if err := s.Reindex(context.Background()); err != nil {
				t.Fatal(err)
			}
			r, err := s.Search(context.Background(), "archives", 0, 20)
			if err != nil || len(r.Hits) == 0 || r.Hits[0].ID != i.ID {
				t.Fatal("fixture did not expose semantic match", r, err)
			}
			n.Title = "Unrelated practice"
			if err := s.Mutate("operator", "revise the reading context, not the item", Batch{
				Nodes: []NodeChange{{ExpectedRevision: 1, Node: n}},
			}); err != nil {
				t.Fatal(err)
			}
			st, err := s.Read()
			if err != nil || st.Items[i.ID].Revision != 1 {
				t.Fatal("fixture unexpectedly revised item", err)
			}
			if rebuild {
				if err := s.Reindex(context.Background()); err != nil {
					t.Fatal(err)
				}
			}
			r, err = s.Search(context.Background(), "archives", 0, 20)
			if err != nil {
				t.Fatal(err)
			}
			for _, hit := range r.Hits {
				if hit.ID == i.ID {
					t.Fatal("old node-title meaning survived as current semantic relevance", hit)
				}
			}
			if !rebuild && r.StaleEntries != 1 {
				t.Fatal("missing stale dependency report", r)
			}
			if rebuild && r.StaleEntries != 0 {
				t.Fatal("rebuild left stale entry", r)
			}
		})
	}
}

func TestSemanticTitleChangeAcrossActivationContext(t *testing.T) {
	s, n, i := semanticTitleFixture(t)
	running(t, s)
	a, _, err := s.Admit()
	if err != nil {
		t.Fatal(err)
	}
	check := func(store *Store, actor string, want bool) {
		t.Helper()
		p, err := store.RefreshContext(context.Background(), actor, "archives")
		if err != nil {
			t.Fatal(err)
		}
		found := false
		why := ""
		for _, item := range p.Items {
			found = found || item.Item.ID == i.ID
			if item.Item.ID == i.ID {
				why = item.Why
			}
		}
		if found != want {
			t.Fatalf("remote semantic candidate present=%v, want=%v; phase=%s; why=%s", found, want, p.Phase, why)
		}
	}
	check(s, a.ID, true)
	n.Title = "Unrelated practice"
	if err := s.Mutate(a.ID, "new evidence revises context", Batch{Nodes: []NodeChange{{ExpectedRevision: 1, Node: n}}}); err != nil {
		t.Fatal(err)
	}
	if err := s.BeginRectification(a.ID, "context corrected", "synthetic-session"); err != nil {
		t.Fatal(err)
	}
	check(s, a.ID, false)
	complete(t, s, a, "continue")
	restarted := Open(s.Dir)
	restarted.Now = s.Now
	next, _, err := restarted.Admit()
	if err != nil {
		t.Fatal(err)
	}
	check(restarted, next.ID, false)
	complete(t, restarted, next, "wait")
}

func TestSemanticLegacyIndexRebuildPreservesCanonicalState(t *testing.T) {
	s, _, i := semanticTitleFixture(t)
	if err := s.Reindex(context.Background()); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(s.path("search-index.json"))
	if err != nil {
		t.Fatal(err)
	}
	var index map[string]any
	if err := json.Unmarshal(raw, &index); err != nil {
		t.Fatal(err)
	}
	entries := index["entries"].(map[string]any)
	for _, entry := range entries {
		delete(entry.(map[string]any), "input_hash")
	}
	if err := Atomic(s.path("search-index.json"), Marshal(index)); err != nil {
		t.Fatal(err)
	}
	before, err := os.ReadFile(s.path("state.json"))
	if err != nil {
		t.Fatal(err)
	}
	r, err := s.Search(context.Background(), "archives", 0, 20)
	if err != nil || r.StaleEntries != len(entries) {
		t.Fatal("legacy entries silently accepted", r, err)
	}
	for _, hit := range r.Hits {
		if hit.ID == i.ID {
			t.Fatal("unqualified legacy semantic relevance", hit)
		}
	}
	if err := s.Reindex(context.Background()); err != nil {
		t.Fatal(err)
	}
	r, err = s.Search(context.Background(), "archives", 0, 20)
	if err != nil || r.StaleEntries != 0 || len(r.Hits) == 0 || r.Hits[0].ID != i.ID {
		t.Fatal("legacy rebuild failed", r, err)
	}
	after, err := os.ReadFile(s.path("state.json"))
	if err != nil || !bytes.Equal(before, after) {
		t.Fatal("rebuilding derived cache changed canonical memory", err)
	}
}
