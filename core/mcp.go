package core

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"io"
)

// Stdio JSON-RPC MCP. No public listener and no externally selected private loci.
func (s *Store) MCP(ctx context.Context, in io.Reader, out io.Writer, actor string) error {
	scanner := bufio.NewScanner(in)
	scanner.Buffer(make([]byte, 4096), 2<<20)
	enc := json.NewEncoder(out)
	for scanner.Scan() {
		var req struct {
			JSONRPC string          `json:"jsonrpc"`
			ID      json.RawMessage `json:"id"`
			Method  string          `json:"method"`
			Params  json.RawMessage `json:"params"`
		}
		if e := json.Unmarshal(scanner.Bytes(), &req); e != nil {
			_ = enc.Encode(map[string]any{"jsonrpc": "2.0", "id": nil, "error": map[string]any{"code": -32700, "message": "invalid JSON"}})
			continue
		}
		if len(req.ID) == 0 {
			continue
		}
		var result any
		var err error
		switch req.Method {
		case "initialize":
			result = map[string]any{"protocolVersion": "2024-11-05", "capabilities": map[string]any{"tools": map[string]any{}}, "serverInfo": map[string]any{"name": "concorde3", "version": "0.1.0"}}
		case "ping":
			result = map[string]any{}
		case "tools/list":
			result = map[string]any{"tools": Tools()}
		case "tools/call":
			var p struct {
				Name      string          `json:"name"`
				Arguments json.RawMessage `json:"arguments"`
			}
			err = json.Unmarshal(req.Params, &p)
			if err == nil {
				if len(p.Arguments) == 0 {
					p.Arguments = json.RawMessage(`{}`)
				}
				var value any
				value, err = s.Call(ctx, actor, p.Name, p.Arguments)
				if err == nil {
					b, _ := json.Marshal(value)
					st, e := s.Read()
					if e != nil {
						return e
					}
					if len(b) > st.Config.ResponseBytes {
						err = fmt.Errorf("response exceeds configured bound; request fewer items or a smaller slice")
					} else {
						result = map[string]any{"content": []any{map[string]any{"type": "text", "text": string(b)}}, "isError": false}
					}
				}
				if err != nil {
					result = map[string]any{"content": []any{map[string]any{"type": "text", "text": err.Error()}}, "isError": true}
					err = nil
				}
			}
		default:
			err = fmt.Errorf("method not found")
		}
		response := map[string]any{"jsonrpc": "2.0", "id": req.ID}
		if err != nil {
			response["error"] = map[string]any{"code": -32601, "message": err.Error()}
		} else {
			response["result"] = result
		}
		if e := enc.Encode(response); e != nil {
			return e
		}
	}
	return scanner.Err()
}
