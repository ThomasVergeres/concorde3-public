package core

import (
	"context"
	"os"
	"strings"
	"testing"
	"time"
)

func TestCompactWatchReferenceSurvivesCompleteLoopAndLegacyState(t *testing.T) {
	s, path := watchFixture(t)
	legacy := "watch." + strings.Repeat("a", 64)
	if err := s.Notify("purpose", legacy, "Existing long reference remains usable"); err != nil {
		t.Fatal(err)
	}
	if _, err := s.RegisterWatch(context.Background(), "operator", "inbox", "purpose", "Observe new need", WatchSpec{Path: path, IntervalSeconds: 5}); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("new need"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := s.SampleWatch(context.Background(), "inbox"); err != nil {
		t.Fatal(err)
	}
	a, p, err := s.Admit()
	if err != nil {
		t.Fatal(err)
	}
	var ids []string
	for _, wake := range p.Wakes {
		ids = append(ids, wake.ID)
		if wake.ID != legacy && len(wake.ID) > 38 {
			t.Fatalf("unnecessarily long operational reference: %s", wake.ID)
		}
	}
	if len(ids) != 2 {
		t.Fatal("legacy or new observation lost", ids)
	}
	if err := s.BeginRectification(a.ID, "Observed both current sources", ""); err != nil {
		t.Fatal(err)
	}
	st, _ := s.Read()
	if err := s.CompletePhase(a.ID, Completion{ExpectedSeq: st.Seq, Considered: ids, Continuation: "dormant", Coverage: "No further work owed", Reason: "Sources accounted for"}); err != nil {
		t.Fatal(err)
	}
	if err := s.Finish(a.ID, Outcome{Summary: "Retained exact references"}, nil); err != nil {
		t.Fatal(err)
	}
	s = Open(s.Dir)
	st, err = s.Read()
	if err != nil {
		t.Fatal(err)
	}
	for _, id := range ids {
		if st.Wakes[id].ConsumedBy != a.ID {
			t.Fatal("reference changed across restart", id)
		}
	}
	if len(st.Programs["inbox"].Observed.Fingerprint) != 64 {
		t.Fatal("source integrity fingerprint was shortened")
	}
}

func TestCompactWatchReferenceCollisionNeverOverwritesEvidence(t *testing.T) {
	digest := strings.Repeat("a", 64)
	short, full := "watch."+digest[:32], "watch."+digest
	wakes := map[string]Wake{short: {ID: short, Evidence: "Existing unrelated evidence"}}
	if got, err := watchReference(wakes, digest); err != nil || got != full {
		t.Fatal(got, err)
	}
	wakes[full] = Wake{ID: full, Evidence: "Another existing observation"}
	if _, err := watchReference(wakes, digest); err == nil {
		t.Fatal("collision accepted")
	}
	if len(wakes) != 2 || wakes[short].Evidence != "Existing unrelated evidence" {
		t.Fatal("evidence changed")
	}
}

func TestWorkContextExplainsIndependentProgramLifetime(t *testing.T) {
	s := fixture(t)
	running(t, s)
	_, packet, err := s.Admit()
	if err != nil {
		t.Fatal(err)
	}
	for _, entry := range packet.Items {
		if entry.Item.ID == "capabilities" {
			if len([]byte(entry.Item.Text)) > 906 {
				t.Fatal("default capability prose grew beyond its preexisting context footprint")
			}
			if !strings.Contains(entry.Item.Text, "between cognitive activations") || !strings.Contains(entry.Item.Text, "do not consume cognitive starts") {
				t.Fatal("initial work context omits ordinary independent execution semantics")
			}
			return
		}
	}
	t.Fatal("required capability context absent")
}

func TestUnallocatedIntentionConsequenceSurvivesWritebackRestart(t *testing.T) {
	s := fixture(t)
	target := Item{ID: "renewal", Node: "root", Kind: "intention", Text: "Review existing supplier renewal next week", Status: "active"}
	st, _ := s.Read()
	target.Node = st.Items["purpose"].Node
	if err := s.Mutate("operator", "Existing unallocated concern", Batch{Items: []ItemChange{{Item: target}}}); err != nil {
		t.Fatal(err)
	}
	running(t, s)
	a, _, err := s.Admit()
	if err != nil {
		t.Fatal(err)
	}
	if err = s.BeginRectification(a.ID, "Local supplier change may affect the distant renewal", ""); err != nil {
		t.Fatal(err)
	}
	consequence := Consequence{ID: "supplier-implication", Summary: "Inspect renewal's dependence on this supplier", References: []string{"renewal"}, Targets: []string{"renewal"}}
	if err = s.Mutate(a.ID, "Preserve implication without inventing capacity", Batch{Consequences: []Consequence{consequence}}); err != nil {
		t.Fatal(err)
	}
	st, _ = s.Read()
	if st.Items["renewal"].Attention != nil || st.Portfolio()["renewal"].ID != "" {
		t.Fatal("memory preservation minted attention")
	}
	if err = s.CompletePhase(a.ID, Completion{ExpectedSeq: st.Seq, Considered: []string{"renewal"}, Outstanding: []string{consequence.ID}, Continuation: "dormant", Coverage: "No automatic return requested", Reason: "Preserve a referenced implication"}); err != nil {
		t.Fatal(err)
	}
	if err = s.Finish(a.ID, Outcome{Summary: "Implication retained"}, nil); err != nil {
		t.Fatal(err)
	}
	reopened := Open(s.Dir)
	reopened.Now = s.Now
	if err = reopened.Notify("purpose", "review", "Independent useful review"); err != nil {
		t.Fatal(err)
	}
	_, packet, err := reopened.Admit()
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(Marshal(packet)), consequence.ID) {
		t.Fatal("explicit outstanding implication lost after restart")
	}
}

func TestCompletionAcceptsKnownSourceAndTimerWithoutInventedEvidence(t *testing.T) {
	s := fixture(t)
	st, _ := s.Read()
	item := st.Items["purpose"]
	original := s.Now().Add(-time.Hour)
	item.Sources = []Source{{Ref: "/exchange/provider.json", ObservedAt: original}}
	timer := Timer{ID: "review-timer", Pursuit: "purpose", Due: s.Now().Add(time.Minute), Reason: "Optional future review"}
	if err := s.Mutate("operator", "Existing evidence and inactive timer", Batch{ExpectedSeq: &st.Seq, Items: []ItemChange{{ExpectedRevision: item.Revision, Item: item}}, Timers: []Timer{timer}}); err != nil {
		t.Fatal(err)
	}
	running(t, s)
	a, _, err := s.Admit()
	if err != nil {
		t.Fatal(err)
	}
	if err = s.BeginRectification(a.ID, "Read existing cited source", ""); err != nil {
		t.Fatal(err)
	}
	st, _ = s.Read()
	c := Completion{ExpectedSeq: st.Seq, Considered: []string{"/exchange/not-registered.json"}, Continuation: "dormant", Coverage: "Legitimate rest", Reason: "Source accounted for"}
	if err = s.CompletePhase(a.ID, c); err == nil {
		t.Fatal("invented source accepted")
	}
	c.Considered = []string{"/exchange/provider.json", "review-timer"}
	if err = s.CompletePhase(a.ID, c); err != nil {
		t.Fatal(err)
	}
	if err = s.Finish(a.ID, Outcome{Summary: "No source or timer mutation"}, nil); err != nil {
		t.Fatal(err)
	}
	reopened := Open(s.Dir)
	after, err := reopened.Read()
	if err != nil {
		t.Fatal(err)
	}
	if after.HasRef("/exchange/provider.json") || after.HasRef(timer.ID) {
		t.Fatal("considered reference became graph endpoint")
	}
	if !after.Items["purpose"].Sources[0].ObservedAt.Equal(original) || after.Timers[timer.ID].Active {
		t.Fatal("completion mutated source age or scheduled work")
	}
}

func TestWrongReferenceKindGivesCanonicalRetrievalRoute(t *testing.T) {
	s := fixture(t)
	if err := s.Mutate("operator", "Pending observation", Batch{Consequences: []Consequence{{ID: "gap", Summary: "Unverified implication", Targets: []string{"purpose"}}}}); err != nil {
		t.Fatal(err)
	}
	_, err := s.Call(context.Background(), "operator", "item", Marshal(map[string]any{"id": "gap"}))
	if err == nil || !strings.Contains(err.Error(), "consequences") || !strings.Contains(err.Error(), "record") {
		t.Fatal("existing non-item misreported as nonexistent", err)
	}
}
