package core

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
)

func TestHistoryPaginationEndsAndDoesNotInventTotal(t *testing.T) {
	s := fixture(t)
	if err := s.Update("operator", "config", "exercise bounded history pages", func(st *State) error {
		st.Config.ResponseBytes = 1024
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	i := itemFixture("history-source", strings.Repeat("東京 🧭", 100))
	for revision := 0; revision < 2; revision++ {
		if err := s.Mutate("operator", "retain revision", Batch{Items: []ItemChange{{ExpectedRevision: revision, Item: i}}}); err != nil {
			t.Fatal(err)
		}
	}
	for _, tc := range []struct {
		id                  string
		offset, count, next int
	}{
		{i.ID, 0, 1, 1}, {i.ID, 1, 1, -1}, {i.ID, 2, 0, -1}, {i.ID, 99, 0, -1}, {"absent", 0, 0, -1},
	} {
		value, err := s.Call(context.Background(), "operator", "history", Marshal(map[string]any{"id": tc.id, "offset": tc.offset, "limit": 1}))
		if err != nil {
			t.Fatal(err)
		}
		page := value.(pageResult)
		if page.Total != -1 {
			t.Fatal("partial scan presented as archive total", page.Total)
		}
		if page.Next != tc.next || len(page.Items) != tc.count || len(Marshal(page)) > 1024 {
			t.Fatal("invalid bounded history page", tc, page)
		}
		for _, row := range page.Items {
			ref, ok := row.(map[string]any)
			if !ok || ref["oversized"] != true {
				t.Fatal("large event lacks a retrieval reference", row)
			}
			text := collectTextPages(t, s, "event", map[string]any{"sequence": ref["sequence"], "limit": 73}, "text")
			var event Event
			if err := json.Unmarshal([]byte(text), &event); err != nil {
				t.Fatal(err)
			}
			if event.Seq != ref["sequence"].(int64) || len(event.Changes) != 1 {
				t.Fatal("wrong referenced event", event)
			}
			var recovered Item
			if err := json.Unmarshal(event.Changes[0].Value, &recovered); err != nil {
				t.Fatal(err)
			}
			if recovered.Text != i.Text || recovered.Revision != tc.offset+1 {
				t.Fatal("omitted event did not retain exact evidence", recovered)
			}
		}
	}
}

func TestHistoryReferenceSurvivesLargeMetadata(t *testing.T) {
	s := fixture(t)
	if err := s.Update("operator", "config", "small response budget", func(st *State) error {
		st.Config.ResponseBytes = 1024
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	i := itemFixture("large-history-reason", "Original evidence remains available.")
	reason := strings.Repeat("A detailed revision reason. ", 60)
	if err := s.Mutate("operator", reason, Batch{Items: []ItemChange{{Item: i}}}); err != nil {
		t.Fatal(err)
	}
	value, err := s.Call(context.Background(), "operator", "history", Marshal(map[string]any{"id": i.ID}))
	if err != nil {
		t.Fatal(err)
	}
	page := value.(pageResult)
	if len(page.Items) != 1 || page.Next != -1 {
		t.Fatal("missing terminal history reference", page)
	}
	ref, ok := page.Items[0].(map[string]any)
	if !ok || ref["sequence"] == nil {
		t.Fatal("large event metadata lost its immutable retrieval reference", page.Items[0])
	}
	text := collectTextPages(t, s, "event", map[string]any{"sequence": ref["sequence"], "limit": 300}, "text")
	var event Event
	if err := json.Unmarshal([]byte(text), &event); err != nil || event.Reason != reason {
		t.Fatal("full original reason not recoverable", err)
	}
}
