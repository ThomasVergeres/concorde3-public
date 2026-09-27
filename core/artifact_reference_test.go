package core

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func artifactReferences(t *testing.T, value any, requested, workspace string) map[string]any {
	t.Helper()
	v := value.(map[string]any)
	if v["path"] != requested || v["workspace_path"] != workspace {
		t.Fatalf("artifact result lacks distinct tool/workspace references: %#v", v)
	}
	if filepath.IsAbs(v["path"].(string)) || filepath.IsAbs(v["workspace_path"].(string)) {
		t.Fatal("artifact result exposed an absolute path")
	}
	return v
}

func artifactWorkspace(t *testing.T, s *Store, responseBytes int) {
	t.Helper()
	if err := s.Update("operator", "config", "artifact reference test", func(st *State) error {
		st.Config.Workspace = true
		if responseBytes != 0 {
			st.Config.ResponseBytes = responseBytes
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
}

func TestArtifactReferencesWriteReadAndUnicodePages(t *testing.T) {
	s := fixture(t)
	artifactWorkspace(t, s, 1024)
	ctx := context.Background()
	name := "nested/../nested/東京/response.txt"
	want := "artifacts/nested/東京/response.txt"
	content := strings.Repeat("\x00<&>東京🧭\n", 200)
	written, err := s.Call(ctx, "operator", "artifact", Marshal(map[string]any{"path": name, "write": content}))
	if err != nil {
		t.Fatal(err)
	}
	v := artifactReferences(t, written, name, want)
	if v["written"] != true {
		t.Fatal("write did not complete")
	}
	if bytes.Contains(Marshal(v), []byte(s.Dir)) {
		t.Fatal("host workspace path leaked")
	}
	for _, misleading := range []string{"delivered", "accepted", "verified"} {
		if _, exists := v[misleading]; exists {
			t.Fatal("local write invented downstream outcome", misleading)
		}
	}
	var collected strings.Builder
	offset := 0
	for {
		page, err := s.Call(ctx, "operator", "artifact", Marshal(map[string]any{"path": name, "offset": offset, "limit": 251}))
		if err != nil {
			t.Fatal(err)
		}
		v := artifactReferences(t, page, name, want)
		if len(Marshal(v)) > 1024 {
			t.Fatal("artifact references escaped response budget")
		}
		collected.WriteString(v["content"].(string))
		next := v["next_offset"].(int)
		if next == -1 {
			break
		}
		if next <= offset {
			t.Fatal("pagination did not progress")
		}
		offset = next
	}
	if collected.String() != content {
		t.Fatal("references altered original artifact bytes")
	}
	empty, err := s.Call(ctx, "operator", "artifact", Marshal(map[string]any{"path": name, "offset": len(content)}))
	if err != nil {
		t.Fatal(err)
	}
	artifactReferences(t, empty, name, want)
	// Do not silently "fix" a path the caller actually supplied. A redundant
	// prefix names a real nested directory; the returned workspace reference says so.
	nested, err := s.Call(ctx, "operator", "artifact", Marshal(map[string]any{"path": "artifacts/file.txt", "write": "nested"}))
	if err != nil {
		t.Fatal(err)
	}
	artifactReferences(t, nested, "artifacts/file.txt", "artifacts/artifacts/file.txt")
}

func TestArtifactReferenceMetadataMustFitBeforeWriting(t *testing.T) {
	s := fixture(t)
	artifactWorkspace(t, s, 1024)
	name := strings.Repeat("a", 180) + "/" + strings.Repeat("b", 180) + "/" + strings.Repeat("c", 180)
	_, err := s.Call(context.Background(), "operator", "artifact", Marshal(map[string]any{"path": name, "write": "never silently written"}))
	if err == nil || !strings.Contains(err.Error(), "response budget") {
		t.Fatal("oversized reference receipt was not rejected before write", err)
	}
	if _, err := os.Stat(filepath.Join(s.Dir, "artifacts", name)); !os.IsNotExist(err) {
		t.Fatal("file was written although reference receipt could not fit", err)
	}
	// A workspace-created file is still accessible by workspace tools when its
	// reference metadata alone cannot fit an artifact-tool response.
	path := filepath.Join(s.Dir, "artifacts", name)
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("existing"), 0600); err != nil {
		t.Fatal(err)
	}
	if value, err := s.Call(context.Background(), "operator", "artifact", Marshal(map[string]any{"path": name})); err == nil {
		t.Fatal("oversized read reference escaped response budget", value)
	}
}

func TestArtifactReferenceNamespaceDoesNotRelaxAuthorityOrPaths(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	if _, err := s.Call(ctx, "operator", "artifact", Marshal(map[string]any{"path": "x", "write": "x"})); err == nil || !strings.Contains(err.Error(), "workspace writes") {
		t.Fatal("namespace metadata granted workspace authority", err)
	}
	artifactWorkspace(t, s, 0)
	for _, name := range []string{"", "../escape", "/absolute.txt"} {
		if _, err := s.Call(ctx, "operator", "artifact", Marshal(map[string]any{"path": name, "write": "x"})); err == nil {
			t.Fatal("invalid artifact path accepted", name)
		}
	}
	if err := os.MkdirAll(filepath.Join(s.Dir, "artifacts"), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(t.TempDir(), filepath.Join(s.Dir, "artifacts", "outside")); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Call(ctx, "operator", "artifact", Marshal(map[string]any{"path": "outside/no", "write": "x"})); err == nil || !strings.Contains(err.Error(), "symlink") {
		t.Fatal("artifact symlink boundary changed", err)
	}
	running(t, s)
	a, _, err := s.Admit()
	if err != nil {
		t.Fatal(err)
	}
	if err = s.BeginRectification(a.ID, "state-only", "session"); err != nil {
		t.Fatal(err)
	}
	if _, err = s.Call(ctx, a.ID, "artifact", Marshal(map[string]any{"path": "x", "write": "x"})); err == nil || !strings.Contains(err.Error(), "state-only") {
		t.Fatal("namespace metadata changed rectification authority", err)
	}
}

func artifactMCP(t *testing.T, s *Store, actor, tool string, arguments any) map[string]any {
	t.Helper()
	input := Marshal(map[string]any{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
		"params": map[string]any{"name": tool, "arguments": arguments}})
	var output bytes.Buffer
	if err := s.MCP(context.Background(), bytes.NewReader(append(input, '\n')), &output, actor); err != nil {
		t.Fatal(err)
	}
	var response struct {
		Result struct {
			Content []struct {
				Text string `json:"text"`
			} `json:"content"`
			IsError bool `json:"isError"`
		} `json:"result"`
	}
	if err := json.Unmarshal(output.Bytes(), &response); err != nil || response.Result.IsError || len(response.Result.Content) != 1 {
		t.Fatal("MCP request failed", output.String(), err)
	}
	var result map[string]any
	if err := json.Unmarshal([]byte(response.Result.Content[0].Text), &result); err != nil {
		t.Fatal(err)
	}
	return result
}

func TestArtifactReferencesThroughMCPWritebackRectificationAndRestart(t *testing.T) {
	s := fixture(t)
	artifactWorkspace(t, s, 0)
	running(t, s)
	a, _, err := s.Admit()
	if err != nil {
		t.Fatal(err)
	}
	args := map[string]any{"path": "deliveries/reply.json", "write": `{"locally_created":true}`}
	written := artifactMCP(t, s, a.ID, "artifact", args)
	if written["deferred"] == true {
		written = artifactMCP(t, s, a.ID, "artifact", args)
	}
	artifactReferences(t, written, "deliveries/reply.json", "artifacts/deliveries/reply.json")
	ref := written["workspace_path"].(string)
	item := itemFixture("local-evidence", "Local file exists; no external delivery has occurred.")
	item.Sources = []Source{{Ref: ref}}
	artifactMCP(t, s, a.ID, "mutate", map[string]any{"reason": "retain exact returned source namespace", "changes": Batch{Items: []ItemChange{{Item: item}}}})
	if err = s.BeginRectification(a.ID, "local write retained, not delivery", "same-session"); err != nil {
		t.Fatal(err)
	}
	page := artifactMCP(t, s, a.ID, "artifact", map[string]any{"path": written["path"]})
	artifactReferences(t, page, "deliveries/reply.json", ref)
	st, _ := s.Read()
	artifactMCP(t, s, a.ID, "phase_complete", map[string]any{"completion": Completion{
		ExpectedSeq: st.Seq, Considered: []string{"local-evidence", ref}, Continuation: "continue",
		Coverage: "Local artifact only; no delivered service asserted", Reason: "Retain actual evidence"}})
	if err = s.Finish(a.ID, Outcome{Summary: "Exact source retained"}, nil); err != nil {
		t.Fatal(err)
	}
	r := Open(s.Dir)
	r.Now = s.Now
	b, _, err := r.Admit()
	if err != nil {
		t.Fatal(err)
	}
	st, _ = r.Read()
	if st.Items["local-evidence"].Sources[0].Ref != ref || st.Items["local-evidence"].Actor != a.ID || len(st.Receipts) != 0 {
		t.Fatal("reference did not survive or invented effect receipt")
	}
	page = artifactMCP(t, r, b.ID, "artifact", map[string]any{"path": "deliveries/reply.json"})
	artifactReferences(t, page, "deliveries/reply.json", ref)
	complete(t, r, b, "wait")
}
