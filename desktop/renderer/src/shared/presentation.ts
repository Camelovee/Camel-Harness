import DOMPurify from "dompurify";
import { marked } from "marked";

import type { ContentBlock } from "./model";

export function errorText(reason: unknown, fallback: string): string {
  return reason instanceof Error ? reason.message : fallback;
}

export function workspaceName(value: string): string {
  const normalized = value.replace(/[\\/]$/, "");
  return normalized.split(/[\\/]/).pop() || value;
}

function blockText(blocks: ContentBlock[], type: string): string {
  return blocks
    .filter((block) => block.type === type)
    .map((block) => block.text ?? "")
    .join("");
}

export function textFromBlocks(blocks: ContentBlock[]): string {
  return blockText(blocks, "text");
}

export function reasoningFromBlocks(blocks: ContentBlock[]): string {
  return blockText(blocks, "reasoning");
}

export function appendBlockText(
  blocks: ContentBlock[],
  type: string,
  delta: string,
): ContentBlock[] {
  const index = blocks.findIndex((block) => block.type === type);
  if (index < 0) return [...blocks, { type, text: delta }];
  return blocks.map((block, blockIndex) =>
    blockIndex === index ? { ...block, text: `${block.text ?? ""}${delta}` } : block,
  );
}

export function renderMarkdown(value: string): string {
  const normalized = value.replace(/\r\n?/g, "\n").replace(/\n+$/, "");
  return DOMPurify.sanitize(
    marked.parse(normalized, { async: false, breaks: true, gfm: true }) as string,
  );
}
