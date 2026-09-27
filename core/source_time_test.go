package core

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"testing"
	"time"
)

func TestSourceTimeAbsentIsNotAnAncientObservation(t *testing.T) {
	for _, input := range []string{`{"ref":"artifact"}`, `{"ref":"artifact","observed_at":"0001-01-01T00:00:00Z"}`} {
		var source Source
		if err := Decode([]byte(input), &source); err != nil {
			t.Fatal(err)
		}
		if got := string(Marshal(source)); got != `{"ref":"artifact"}` {
			t.Fatalf("unknown observation time became dated evidence: %s", got)
		}
	}
	known := `{"ref":"artifact","observed_at":"2026-09-10T20:53:18.611743305Z"}`
	var source Source
	if err := Decode([]byte(known), &source); err != nil {
		t.Fatal(err)
	}
	if string(Marshal(source)) != known {
		t.Fatal("known source time changed")
	}
	if err := Decode([]byte(`{"ref":"artifact","observed_at":"not-a-time"}`), &source); err == nil {
		t.Fatal("invalid time accepted")
	}
	if err := Decode([]byte(`{"ref":"artifact","unknown":true}`), &source); err == nil {
		t.Fatal("strict decoding lost")
	}
}

func TestSourceTimeLegacyJournalRetainedAcrossReplay(t *testing.T) {
	s := fixture(t)
	st, err := s.Read()
	if err != nil {
		t.Fatal(err)
	}
	i := itemFixture("legacy-source", "Source retained without a known observation time")
	i.Revision, i.Actor, i.Reason, i.UpdatedAt = 1, "operator", "Legacy evidence", s.Now()
	// Reproduce the old on-disk representation, independently of the new encoder.
	raw := strings.TrimSuffix(string(Marshal(i)), "}") + `,"sources":[{"ref":"artifact","observed_at":"0001-01-01T00:00:00Z"}]}`
	ev := Event{Seq: st.Seq + 1, At: s.Now(), Actor: "operator", Reason: "Legacy fixture", Kind: "mutation", Changes: []Change{{Field: "items", Key: i.ID, Value: json.RawMessage(raw)}}}
	b := Marshal(ev)
	journal, err := os.ReadFile(s.path("events.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	journal = append(journal, append(Marshal(envelope{Event: b, Hash: Digest(b)}), '\n')...)
	if err = os.WriteFile(s.path("events.jsonl"), journal, 0600); err != nil {
		t.Fatal(err)
	}
	r := Open(s.Dir)
	replayed, err := r.Read()
	if err != nil {
		t.Fatal(err)
	}
	got := replayed.Items[i.ID]
	if got.Sources[0].Ref != "artifact" || !got.Sources[0].ObservedAt.IsZero() || got.Actor != i.Actor {
		t.Fatal("legacy evidence lost")
	}
	if strings.Contains(string(Marshal(got.Sources)), "observed_at") {
		t.Fatal("legacy unknown source still rendered as ancient")
	}
	if err = r.SetMode("running"); err != nil {
		t.Fatal(err)
	}
	after, err := os.ReadFile(s.path("events.jsonl"))
	if err != nil || !strings.HasPrefix(string(after), string(journal)) {
		t.Fatal("immutable historical journal rewritten", err)
	}
	if _, err = Open(s.Dir).Read(); err != nil {
		t.Fatal("new snapshot cannot reopen", err)
	}
}

func TestSourceTimeActualCommandLoop(t *testing.T) {
	s := fixture(t)
	s.Now = func() time.Time { return time.Now().UTC() }
	if err := s.Update("operator", "config", "Source provenance command-loop qualification", func(st *State) error {
		st.Config.Harness = "command"
		st.Config.Command = []string{os.Args[0], "-test.run=TestSourceTimeCommandHelper", "source-time-loop"}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	if err := s.Run(context.Background(), true, nil); err != nil {
		t.Fatal(err)
	}
	r := Open(s.Dir)
	st, err := r.Read()
	if err != nil {
		t.Fatal(err)
	}
	for _, a := range st.Activations {
		if a.Status != "completed" {
			t.Fatal(a.Summary)
		}
	}
	if len(st.Activations) != 1 || len(st.Items["source-evidence"].Sources) != 2 {
		t.Fatal("source/activation state lost")
	}
	running(t, r)
	if err := r.Notify("purpose", "later-source", "Inspect retained provenance in another embodiment"); err != nil {
		t.Fatal(err)
	}
	a, _, err := r.Admit()
	if err != nil {
		t.Fatal(err)
	}
	checkSourceTimeTool(t, r, a.ID)
	complete(t, r, a, "wait")
}

func checkSourceTimeTool(t *testing.T, s *Store, actor string) {
	t.Helper()
	v, err := s.Call(context.Background(), actor, "item", Marshal(map[string]any{"id": "source-evidence"}))
	if err != nil {
		t.Fatal(err)
	}
	var item Item
	if err = json.Unmarshal(Marshal(v), &item); err != nil {
		t.Fatal(err)
	}
	if len(item.Sources) != 2 || item.Sources[0].Ref != "artifact:undated" || !item.Sources[0].ObservedAt.IsZero() || item.Sources[1].ObservedAt.Format(time.RFC3339Nano) != "2026-09-10T20:53:18.611743305Z" {
		t.Fatal("source provenance changed", item.Sources)
	}
	if strings.Contains(string(Marshal(item.Sources)), "0001-01-01") {
		t.Fatal("unknown observation time misrepresented to activation")
	}
}

func TestSourceTimeCommandHelper(t *testing.T) {
	if !strings.Contains(strings.Join(os.Args, " "), "source-time-loop") {
		return
	}
	if os.Args[len(os.Args)-1] == "--concorde-capabilities" {
		fmt.Print(`{"protocol":2,"continuation":true}`)
		os.Exit(0)
	}
	var p Packet
	if err := json.NewDecoder(os.Stdin).Decode(&p); err != nil {
		panic(err)
	}
	s := Open(os.Getenv("CONCORDE3_INSTANCE"))
	out := Outcome{Summary: "Undated source retained without inventing observation time"}
	if p.Phase == "work" {
		changes := `{"reason":"Retain known and unknown source ages","changes":{"items":[{"expected_revision":0,"item":{"id":"source-evidence","node":"undertaking","kind":"observation","text":"Independent sources with different provenance completeness","status":"active","sources":[{"ref":"artifact:undated"},{"ref":"artifact:dated","observed_at":"2026-09-10T20:53:18.611743305Z"}]}}]}}`
		if _, err := s.Call(context.Background(), p.Activation, "mutate", []byte(changes)); err != nil {
			panic(err)
		}
	} else {
		checkSourceTimeTool(t, s, p.Activation)
		st, err := s.Read()
		if err != nil {
			panic(err)
		}
		out.Completion = &Completion{ExpectedSeq: st.Seq, Considered: []string{"source-evidence"}, Continuation: "wait", Coverage: "No further work due", Reason: "Retain source reference without timestamp fabrication"}
	}
	if err := json.NewEncoder(os.Stdout).Encode(out); err != nil {
		panic(err)
	}
	os.Exit(0)
}
