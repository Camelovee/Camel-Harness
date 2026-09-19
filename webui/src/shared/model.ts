export type Session = {
  session_id: string;
  name: string;
  workspace: string;
  updated_at: string;
  message_count: number;
  archived: boolean;
};

export type Workspace = {
  workspace: string;
  updated_at: string;
  sessions: Session[];
};

export type ContentBlock = {
  type: string;
  text?: string;
  call_id?: string;
  name?: string;
  content?: string;
  is_error?: boolean;
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

export type Message = {
  id: string;
  role: "user" | "assistant" | "toolResult" | "approval";
  content: ContentBlock[];
  approval?: Approval;
};

export type Activity = {
  id: string;
  name: string;
  status: "running" | "success" | "error";
  result?: string;
};

export type WireMessage =
  | { role: "user"; content: string }
  | { role: "assistant"; content: ContentBlock[]; stop_reason: string }
  | {
      role: "toolResult";
      tool_call_id: string;
      tool_name: string;
      content: string;
      is_error: boolean;
    };

export type AssistantStreamEvent = {
  type: string;
  data: Record<string, unknown>;
};

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
export type WorkbenchTab = "chat" | "trajectory";
