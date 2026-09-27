package core

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// Diagnostic only: report actual retrieval from a private historical state.
// Do not make omitted corrections a desired regression invariant.
func TestPrivateRecurrenceRetrievalQualification(t *testing.T) {
	dir := os.Getenv("C3_RECURRENCE_RETRIEVAL_REPLAY")
	if dir == "" {
		t.Skip("private recurrence snapshot not supplied")
	}
	var st State
	var original Packet
	for name, target := range map[string]any{"pre-admission-state.json": &st, "original-work-context.json": &original} {
		b, err := os.ReadFile(filepath.Join(dir, name))
		if err != nil {
			t.Fatal(err)
		}
		if err = json.Unmarshal(b, target); err != nil {
			t.Fatal(err)
		}
	}
	const correction = "maya-occasion-2026-09-16-2026"
	if st.Seq != 139 || st.Items[correction].ID != correction {
		t.Fatal("unexpected recurrence boundary")
	}
	s := fixture(t)
	s.Now = func() time.Time { return original.At }
	if err := s.Update("operator", "fixture", "Isolated historical retrieval diagnosis", func(target *State) error {
		*target = clone(st)
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	purpose := st.Items[original.Pursuit.ID]
	query := purpose.Text + " " + purpose.Approach + "  "
	result, err := s.Search(context.Background(), query, 0, 100)
	if err != nil {
		t.Fatal(err)
	}
	required := map[string]bool{}
	for _, id := range st.Config.Global {
		required[id] = true
	}
	required[st.Config.WorkPractice] = true
	required[st.Config.RectifyPractice] = true
	duplicates, rank := 0, 0
	for index, hit := range result.Hits {
		if hit.ID == correction {
			rank = index + 1
		}
		if index < 12 && required[hit.ID] {
			duplicates++
		}
		t.Logf("rank=%d id=%s score=%.6f already-bound-or-inactive-practice=%t", index+1, hit.ID, hit.Score, required[hit.ID])
	}
	if rank == 0 {
		t.Fatal("correction absent even from complete lexical result")
	}
	t.Logf("correction rank=%d; top12 bound/inactive candidates=%d; semantic=%s", rank, duplicates, result.Semantic)
}
