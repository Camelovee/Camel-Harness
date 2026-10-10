export type Session = {
  session_id: string;
  name: string;
  workspace: string;
  updated_at: string;
  message_count: number;
  archived: boolean;
  model_id: string | null;
};

export type SessionIndicator = {
  running: boolean;
  approvalCount: number;
  unread: boolean;
  failed: boolean;
  cancelled: boolean;
};

export type ModelProfile = {
  id: string;
  name: string;
  provider: "anthropic" | "openai";
  model: string;
  base_url: string | null;
  configured: boolean;
  has_api_key: boolean;
  read_only: boolean;
};

export type WebSettings = {
  models: ModelProfile[];
  default_model_id: string;
  thinking: boolean;
};

export type ModelInput = {
  name: string;
  provider: ModelProfile["provider"];
  model: string;
  base_url: string;
  api_key: string;
  clear_api_key: boolean;
};

export type Workspace = {
  workspace: string;
  updated_at: string;
  sessions: Session[];
};

export type ToolResultDetails = {
  content_ref?: {
    path: string;
    mime_type: string;
    size_bytes: number;
    original_chars: number;
    preview_chars: number;
  };
  [key: string]: unknown;
};

export type LlmUsage = {
  input_tokens: number | null;
  output_tokens: number | null;
  cache_read_tokens: number | null;
  cache_write_tokens: number | null;
  reasoning_tokens: number | null;
  total_tokens: number | null;
};

export type AssistantAudit = {
  provider?: string;
  model?: string;
  response_model?: string;
  usage?: LlmUsage;
};

export type ContentBlock = {
  type: string;
  text?: string;
  call_id?: string;
  name?: string;
  content?: string;
  is_error?: boolean;
  details?: ToolResultDetails;
};

export type ApprovalStatus = "pending" | "approved" | "rejected" | "completed" | "failed";

export type Approval = {
  request_id: string;
  session_id?: string;
  call_id?: string;
  tool_name: string;
  capabilities?: string[];
  risk?: string;
  arguments?: Record<string, unknown>;
  reason?: string;
  status: ApprovalStatus;
};

export type Message = AssistantAudit & {
  id: string;
  role: "user" | "assistant" | "toolResult" | "approval";
  content: ContentBlock[];
  approval?: Approval;
};

export type TokenUsage = {
  input: number | null;
  output: number | null;
  runTotal: number | null;
};

export type WireMessage =
  | { role: "user"; content: string }
  | (AssistantAudit & { role: "assistant"; content: ContentBlock[]; stop_reason: string })
  | {
      role: "toolResult";
      tool_call_id: string;
      tool_name: string;
      content: string;
      is_error: boolean;
      details?: ToolResultDetails;
    };

export type AssistantStreamEvent =
  | { type: "stream_started"; model: string | null }
  | { type: "text_delta" | "reasoning_delta"; text: string }
  | { type: "tool_call_started"; call_id: string; name: string }
  | { type: "tool_call_arguments_delta"; call_id: string; delta: string }
  | { type: "tool_call_completed"; tool_call: { call_id: string; name: string; arguments: Record<string, unknown> } }
  | { type: "usage_updated"; usage: Omit<LlmUsage, "total_tokens"> }
  | { type: "message_completed"; stop_reason: "stop" | "length" | "tool_use"; model: string | null }
  | { type: "stream_failed"; error: string; retryable: boolean; code: "context_overflow" | null };

type AgentEventBase = {
  session_id: string;
  run_id: string;
  sequence: number;
};

type TurnEventBase = AgentEventBase & { turn_id: string };

export type AgentEvent =
  | (AgentEventBase & { type: "agent_start" })
  | (AgentEventBase & {
      type: "agent_end";
      status: "completed" | "failed" | "cancelled";
      stop_reason: string | null;
      total_tokens: number;
      error: string | null;
    })
  | (TurnEventBase & { type: "turn_start"; turn: number })
  | (TurnEventBase & {
      type: "turn_end";
      message: WireMessage;
      tool_results: WireMessage[];
    })
  | (TurnEventBase & {
      type: "message_start";
      message_id: string;
      role: "user" | "assistant" | "toolResult";
    })
  | (TurnEventBase & {
      type: "message_update";
      message_id: string;
      assistant_event: AssistantStreamEvent;
    })
  | (TurnEventBase & {
      type: "message_end";
      message_id: string;
      message: WireMessage;
    })
  | (TurnEventBase & {
      type: "tool_execution_start";
      call_id: string;
      name: string;
      arguments: Record<string, unknown>;
    })
  | (TurnEventBase & {
      type: "tool_execution_end";
      result: {
        call_id: string;
        name: string;
        content: string;
        is_error: boolean;
      };
    })
  | (TurnEventBase & {
      type: "approval_requested";
      request_id: string;
      call_id: string;
      name: string;
      capabilities: string[];
      risk: string;
      arguments: Record<string, unknown>;
      reason: string;
    });

export type PermissionMode = "default" | "accept_edits" | "plan" | "yolo";
