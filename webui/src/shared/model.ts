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

export type StreamEvent = {
  type: string;
  session_id?: string;
  step_id?: string;
  sequence: number;
  data: Record<string, unknown>;
};

export type PermissionMode = "default" | "accept_edits" | "plan" | "yolo";
export type WorkbenchTab = "chat" | "trajectory";
