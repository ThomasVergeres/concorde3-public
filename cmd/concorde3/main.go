package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/ThomasVergeres/concorde3-public/core"
)

func main() {
	if e := run(os.Args[1:]); e != nil {
		fmt.Fprintln(os.Stderr, e)
		os.Exit(1)
	}
}
func printJSON(v any) error {
	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	return enc.Encode(v)
}
func run(args []string) error {
	if len(args) < 1 {
		return fmt.Errorf("usage: concorde3 init [flags] DIR | run [flags] DIR | pulse DIR | status DIR | call DIR TOOL JSON | mcp DIR [ACTIVATION] | pause/resume/freeze DIR | configure DIR JSON | notify DIR INTENTION_ID KEY EVIDENCE | program-stop DIR ID | history DIR [ID]")
	}
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	switch args[0] {
	case "init":
		fs := flag.NewFlagSet("init", flag.ContinueOnError)
		goal := fs.String("goal", "Understand and develop a useful undertaking", "Initial desired condition")
		workspace := fs.Bool("workspace", false, "Explicitly enable workspace writes and programs")
		if e := fs.Parse(args[1:]); e != nil {
			return e
		}
		if fs.NArg() != 1 {
			return fmt.Errorf("init requires instance directory")
		}
		s := core.Open(fs.Arg(0))
		if e := s.Init(*goal); e != nil {
			return e
		}
		if *workspace {
			if e := s.Update("operator", "config", "explicit workspace permission", func(st *core.State) error { st.Config.Workspace = true; return nil }); e != nil {
				return e
			}
		}
		return printJSON(map[string]any{"instance": s.Dir, "mode": "paused"})
	case "run":
		fs := flag.NewFlagSet("run", flag.ContinueOnError)
		until := fs.String("until", "", "Hard UTC RFC3339 freeze time")
		if e := fs.Parse(args[1:]); e != nil {
			return e
		}
		if fs.NArg() != 1 {
			return fmt.Errorf("run requires directory")
		}
		s := core.Open(fs.Arg(0))
		if *until != "" {
			t, e := time.Parse(time.RFC3339, *until)
			if e != nil {
				return e
			}
			if !t.After(time.Now()) {
				return fmt.Errorf("freeze time must be future")
			}
			if e = s.Update("operator", "config", "finite run deadline", func(st *core.State) error { st.Config.FreezeAt = t; return nil }); e != nil {
				return e
			}
		}
		return s.Run(ctx, false, func(m string) { fmt.Fprintln(os.Stderr, m) })
	}
	if len(args) < 2 {
		return fmt.Errorf("instance directory required")
	}
	s := core.Open(args[1])
	switch args[0] {
	case "pulse":
		return s.Run(ctx, true, nil)
	case "mcp":
		actor := "operator"
		if len(args) > 2 {
			actor = args[2]
		}
		return s.MCP(ctx, os.Stdin, os.Stdout, actor)
	case "call":
		if len(args) != 4 {
			return fmt.Errorf("call DIR TOOL JSON")
		}
		actor := os.Getenv("CONCORDE3_ACTIVATION")
		if actor == "" {
			actor = "operator"
		}
		v, e := s.Call(ctx, actor, args[2], json.RawMessage(args[3]))
		if e != nil {
			return e
		}
		return printJSON(v)
	case "status":
		st, e := s.Read()
		if e != nil {
			return e
		}
		return printJSON(map[string]any{"id": st.Config.ID, "mode": st.Mode, "sequence": st.Seq, "freeze_at": st.Config.FreezeAt, "attention": st.Portfolio(), "activations": st.Activations, "programs": st.Programs, "starts": st.Starts})
	case "watch-source":
		if len(args) != 3 {
			return fmt.Errorf("watch-source DIR PROGRAM_ID")
		}
		return s.WatchLoop(ctx, args[2])
	case "configure":
		if len(args) != 3 {
			return fmt.Errorf("configure DIR JSON_CONFIG_PATCH")
		}
		return s.Update("operator", "config", "operator configuration", func(st *core.State) error {
			b, _ := json.Marshal(st.Config)
			var m map[string]json.RawMessage
			_ = json.Unmarshal(b, &m)
			var patch map[string]json.RawMessage
			if e := json.Unmarshal([]byte(args[2]), &patch); e != nil {
				return e
			}
			for k, v := range patch {
				m[k] = v
			}
			return core.Decode(core.Marshal(m), &st.Config)
		})
	case "pause":
		return s.SetMode("paused")
	case "resume":
		return s.SetMode("running")
	case "freeze":
		return s.Freeze("operator requested finite shutdown")
	case "notify":
		if len(args) != 5 {
			return fmt.Errorf("notify DIR INTENTION_ID KEY EVIDENCE")
		}
		return s.Notify(args[2], args[3], args[4])
	case "program-stop":
		if len(args) != 3 {
			return fmt.Errorf("program-stop DIR ID")
		}
		return s.StopProgram(args[2])
	case "history":
		id := ""
		if len(args) > 2 {
			id = args[2]
		}
		events, e := s.History(id, 0, 1000000)
		if e != nil {
			return e
		}
		return printJSON(events)
	default:
		return fmt.Errorf("unknown command %s", args[0])
	}
}
