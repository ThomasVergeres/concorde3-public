package core

import (
	"encoding/json"
	"testing"
)

func TestToolSchemasNeverEmitNullRequired(t *testing.T) {
	var value any
	if err := json.Unmarshal(Marshal(Tools()), &value); err != nil {
		t.Fatal(err)
	}
	var walk func(any)
	walk = func(v any) {
		switch node := v.(type) {
		case map[string]any:
			if required, exists := node["required"]; exists {
				fields, ok := required.([]any)
				if !ok {
					t.Fatalf("required must be an array or omitted, not %T: %+v", required, node)
				}
				for _, field := range fields {
					if _, ok := field.(string); !ok {
						t.Fatal("required field is not a string")
					}
				}
			}
			for _, child := range node {
				walk(child)
			}
		case []any:
			for _, child := range node {
				walk(child)
			}
		}
	}
	walk(value)
}
