package core

import (
	"bytes"
	"context"
	"encoding/json"
	"reflect"
	"strings"
	"testing"
)

func TestCompletionInterfaceNestedSchemaMatchesRuntime(t *testing.T) {
	var completion map[string]any
	for _, tool := range Tools() {
		if tool.Name == "phase_complete" {
			if strings.Contains(tool.Description, "Read contract first") {
				t.Fatal("complete nested schema must not require a separate contract-read ceremony")
			}
			completion = tool.InputSchema["properties"].(map[string]any)["completion"].(map[string]any)
		}
	}
	if completion == nil {
		t.Fatal("missing phase_complete tool")
	}
	properties, ok := completion["properties"].(map[string]any)
	if !ok {
		t.Fatal("completion is an opaque object instead of an actionable nested schema")
	}
	if !reflect.DeepEqual(completion["required"], []string{"expected_seq", "continuation", "coverage", "reason"}) {
		t.Fatal("runtime-required fields not explicit", completion)
	}
	if properties["expected_seq"].(map[string]any)["type"] != "integer" {
		t.Fatal("sequence type omitted")
	}
	continuation := properties["continuation"].(map[string]any)
	if !reflect.DeepEqual(continuation["enum"], []string{"continue", "wait", "dormant", "stop"}) {
		t.Fatal("continuation choices absent")
	}
	for _, phrase := range []string{"next useful work", "not waiting for an observer", "default reconsideration", "event"} {
		if !strings.Contains(continuation["description"].(string), phrase) {
			t.Fatal("ambiguous continuation semantics", phrase, continuation)
		}
	}
	for _, key := range []string{"considered", "outstanding"} {
		field := properties[key].(map[string]any)
		if field["type"] != "array" || field["items"].(map[string]any)["type"] != "string" {
			t.Fatal("reference list schema missing", key)
		}
	}
}

func TestCompletionInterfaceMissingAndStaleSequenceAreDistinctAtomicErrors(t *testing.T) {
	s := fixture(t)
	running(t, s)
	a, _, err := s.Admit()
	if err != nil {
		t.Fatal(err)
	}
	if err = s.BeginRectification(a.ID, "No product work is needed", "temporary-session"); err != nil {
		t.Fatal(err)
	}
	before, _ := s.Read()
	for _, value := range []map[string]any{
		{"continuation": "dormant", "coverage": "No ongoing duty", "reason": "Adequate rest"},
		{"expected_seq": nil, "continuation": "dormant", "coverage": "No ongoing duty", "reason": "Adequate rest"},
	} {
		_, err = s.Call(context.Background(), a.ID, "phase_complete", Marshal(map[string]any{"completion": value}))
		if err == nil || !strings.Contains(err.Error(), "completion.expected_seq is required") || !strings.Contains(err.Error(), "state") || strings.Contains(err.Error(), "sequence conflict") {
			t.Fatal("missing sequence misdiagnosed instead of actionable repair", err)
		}
		after, _ := s.Read()
		if !bytes.Equal(Marshal(before), Marshal(after)) {
			t.Fatal("invalid completion changed durable state")
		}
	}
	if err = s.Notify("purpose", "new-evidence", "New ordinary source evidence arrived"); err != nil {
		t.Fatal(err)
	}
	current, _ := s.Read()
	_, err = s.Call(context.Background(), a.ID, "phase_complete", Marshal(map[string]any{"completion": Completion{ExpectedSeq: before.Seq, Continuation: "dormant", Coverage: "No ongoing duty", Reason: "Adequate rest"}}))
	if err == nil || !strings.Contains(err.Error(), "sequence conflict") || strings.Contains(err.Error(), "is required") {
		t.Fatal("stale nonzero sequence lost conflict semantics", err)
	}
	after, _ := s.Read()
	if !bytes.Equal(Marshal(current), Marshal(after)) {
		t.Fatal("stale completion mutated state")
	}
	_, err = s.Call(context.Background(), a.ID, "phase_complete", Marshal(map[string]any{"completion": Completion{ExpectedSeq: 0, Continuation: "dormant", Coverage: "No ongoing duty", Reason: "Adequate rest"}}))
	if err == nil || !strings.Contains(err.Error(), "sequence conflict") {
		t.Fatal("explicit zero sequence was silently supplied or treated as omitted", err)
	}
	after, _ = s.Read()
	if !bytes.Equal(Marshal(current), Marshal(after)) {
		t.Fatal("explicit zero sequence changed state")
	}
}

func TestCompletionInterfaceMCPNoEditCompletionRestartAndEventReentry(t *testing.T) {
	s := fixture(t)
	running(t, s)
	a, _, err := s.Admit()
	if err != nil {
		t.Fatal(err)
	}
	if err = s.BeginRectification(a.ID, "Legitimate rest, no graph edits needed", "temporary-session"); err != nil {
		t.Fatal(err)
	}
	before, _ := s.Read()
	request := func(arguments map[string]any) map[string]any {
		t.Helper()
		var output bytes.Buffer
		raw := Marshal(map[string]any{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": map[string]any{"name": "phase_complete", "arguments": arguments}})
		if err := s.MCP(context.Background(), strings.NewReader(string(raw)+"\n"), &output, a.ID); err != nil {
			t.Fatal(err)
		}
		var response map[string]any
		if err := json.Unmarshal(output.Bytes(), &response); err != nil {
			t.Fatal(err)
		}
		return response
	}
	missing := request(map[string]any{"completion": map[string]any{"reason": "Adequate rest", "coverage": "Explicitly uncovered", "continuation": "dormant"}})
	if !strings.Contains(string(Marshal(missing)), "completion.expected_seq is required") {
		t.Fatal("MCP hides repair instruction", missing)
	}
	correct := request(map[string]any{"completion": map[string]any{"expected_seq": before.Seq, "reason": "Adequate rest", "coverage": "Explicitly uncovered", "continuation": "dormant"}})
	if strings.Contains(string(Marshal(correct)), `"isError":true`) || correct["error"] != nil {
		t.Fatal("valid empty-reference completion failed", correct)
	}
	if err = s.Finish(a.ID, Outcome{Summary: "Rest without invented work"}, nil); err != nil {
		t.Fatal(err)
	}
	reopened := Open(s.Dir)
	reopened.Now = s.Now
	st, _ := reopened.Read()
	if len(st.Nodes) != len(before.Nodes) || len(st.Items) != len(before.Items) || st.Items["purpose"].Attention.EffortState != "dormant" || st.Activations[a.ID].Status != "completed" {
		t.Fatal("no-edit completion did not survive restart")
	}
	if _, _, err = reopened.Admit(); err == nil {
		t.Fatal("dormancy became immediate work")
	}
	if err = reopened.Notify("purpose", "material-change", "Ordinary source changed"); err != nil {
		t.Fatal(err)
	}
	next, _, err := reopened.Admit()
	if err != nil || next.ID == a.ID {
		t.Fatal("normal event reentry failed", next, err)
	}
}

func TestCompletionInterfaceAcceptsExistingEdgesAndActivationsButNotUnknownRefs(t *testing.T) {
	s := fixture(t)
	if err := s.Mutate("operator", "A useful relation", Batch{Edges: []Edge{{ID: "related", From: "purpose", To: "capabilities", Relation: "uses", Reason: "Current means"}}}); err != nil {
		t.Fatal(err)
	}
	running(t, s)
	a, _, err := s.Admit()
	if err != nil {
		t.Fatal(err)
	}
	if err = s.BeginRectification(a.ID, "Inspected a relationship and current activation", "session"); err != nil {
		t.Fatal(err)
	}
	before, _ := s.Read()
	c := Completion{ExpectedSeq: before.Seq, Considered: []string{"unknown-edge"}, Continuation: "dormant", Coverage: "No current duty", Reason: "Legitimate rest"}
	if err = s.CompletePhase(a.ID, c); err == nil || !strings.Contains(err.Error(), "unknown considered reference") {
		t.Fatal("invented evidence accepted", err)
	}
	after, _ := s.Read()
	if !bytes.Equal(Marshal(before), Marshal(after)) {
		t.Fatal("unknown reference changed state")
	}
	c.Considered = []string{"related", a.ID}
	if err = s.CompletePhase(a.ID, c); err != nil {
		t.Fatal("existing canonical edge/activation cannot be cited", err)
	}
	if before.HasRef("related") || before.HasRef(a.ID) {
		t.Fatal("completion support leaked into graph endpoint authority")
	}
}
