package main

import (
	"encoding/json"
	"fmt"
	"github.com/ThomasVergeres/concorde3-public/core"
	"os"
	"strings"
	"testing"
)

func TestCLICompleteLoop(t *testing.T) {
	dir := t.TempDir()
	if e := run([]string{"init", "--goal", "CLI qualification", dir}); e != nil {
		t.Fatal(e)
	}
	config, _ := json.Marshal(map[string]any{"harness": "command", "command": []string{os.Args[0], "-test.run=TestCLIHelper", "cli-helper"}})
	if e := run([]string{"configure", dir, string(config)}); e != nil {
		t.Fatal(e)
	}
	if e := run([]string{"configure", dir, `{"unknown_field":true}`}); e == nil {
		t.Fatal("accepted unknown config")
	}
	if e := run([]string{"pulse", dir}); e != nil {
		t.Fatal(e)
	}
	st, e := core.Open(dir).Read()
	if e != nil || st.Mode != "paused" || len(st.Activations) != 1 {
		t.Fatal(st.Mode, e)
	}
	if e := run([]string{"freeze", dir}); e != nil {
		t.Fatal(e)
	}
	if e := run([]string{"resume", dir}); e == nil {
		t.Fatal("resumed frozen instance")
	}
}
func TestCLIHelper(t *testing.T) {
	if !strings.Contains(strings.Join(os.Args, " "), "cli-helper") {
		return
	}
	if os.Args[len(os.Args)-1] == "--concorde-capabilities" {
		fmt.Print(`{"protocol":2,"continuation":true}`)
		os.Exit(0)
	}
	var p core.Packet
	_ = json.NewDecoder(os.Stdin).Decode(&p)
	out := core.Outcome{Summary: "CLI subprocess"}
	if p.Phase == "rectification" {
		st, _ := core.Open(os.Getenv("CONCORDE3_INSTANCE")).Read()
		p.Seq = st.Seq
		out.Completion = &core.Completion{ExpectedSeq: p.Seq, Continuation: "wait", Coverage: "No ongoing fixture responsibility", Reason: "complete"}
	}
	_ = json.NewEncoder(os.Stdout).Encode(out)
	os.Exit(0)
}
