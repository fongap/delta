// UX-015 (§33): tool calls render as one-liners. The model does NOT emit a purpose
// per call — the stream is name+args+result — so the sentence is synthesized here from
// per-tool templates. Every template carries an i18n key next to its original English
// phrasing; renderers resolve through resolveHumanLine so the transcript, approval
// cards, and inbox items localize from one source (the English text stays as the
// fallback and is what unit tests assert). `run_shell` is the exception: its optional
// `description` argument is model-written intent and is preferred when present.
// Fallback: "Used <tool> — <short args>".

import { shortArgs } from "./components/ApprovalCard";

// A one-line sentence in three segments so the UI can emphasize the object:
// "Read " + <b>runbook.md</b> + " from the shared folder".
export interface HumanLine {
  pre: string;
  /** i18n key for the leading segment; `pre` doubles as its English fallback. */
  preKey?: string;
  preVars?: Record<string, string | number>;
  obj?: string;
  /** i18n key for the object segment (e.g. the "files" placeholder). */
  objKey?: string;
  post?: string;
  /** i18n key for the trailing segment. */
  postKey?: string;
  postVars?: Record<string, string | number>;
}

// The useI18n().t shape (kept structural so humanize stays import-cycle-free).
export type HumanizeT = (
  key: string,
  vars?: Record<string, string | number>,
  fallback?: string,
) => string;

// Resolve a line's keyed segments against the active locale; unkeyed segments pass
// through verbatim (raw model-written descriptions, ids, statuses). Shared by every
// HumanLine renderer so they can never drift apart.
export function resolveHumanLine(line: HumanLine, t: HumanizeT): HumanLine {
  return {
    pre: line.preKey ? t(line.preKey, line.preVars, line.pre) : line.pre,
    obj: line.objKey ? t(line.objKey, undefined, line.obj) : line.obj,
    post: line.postKey ? t(line.postKey, line.postVars, line.post) : line.post,
  };
}

const trunc = (s: string, n: number) => (s.length > n ? s.slice(0, n - 1) + "…" : s);
const baseName = (p: string) => p.replace(/\/+$/, "").split("/").pop() || p;

// send_message targets are "platform:chat" or "platform:chat:thread" — show the platform
// by name and the last human-ish segment of the chat id.
function messageTarget(target: string): { platform: string; tail: string } {
  const [platform, ...rest] = String(target).split(":");
  const chat = rest[0] || "";
  const tail = chat.includes("/") ? chat.split("/").pop() || chat : chat;
  const names: Record<string, string> = { slack: "Slack", telegram: "Telegram" };
  return { platform: names[platform] || platform, tail };
}

export function humanizeTool(name: string, args: any): HumanLine {
  const a = args && typeof args === "object" ? args : {};
  switch (name) {
    case "run_shell": {
      const cmd = trunc(String(a.command ?? ""), 60);
      const desc = typeof a.description === "string" && a.description.trim() ? a.description.trim() : "";
      return {
        pre: a.run_in_background ? "Started in the background: " : "Ran ",
        preKey: a.run_in_background ? "humanize.startedBackground" : "humanize.ran",
        obj: cmd,
        ...(desc ? { post: ` — ${desc.charAt(0).toLowerCase()}${desc.slice(1)}` } : {}),
      };
    }
    case "shell_task_output":
      return { pre: "Checked on a background command", preKey: "humanize.checkBackground" };
    case "shell_task_kill":
      return { pre: "Stopped a background command", preKey: "humanize.stopBackground" };
    case "read_file":
      return { pre: "Read ", preKey: "humanize.read", obj: baseName(String(a.path ?? "a file")) };
    case "write_file":
      return { pre: "Wrote ", preKey: "humanize.wrote", obj: baseName(String(a.path ?? "a file")) };
    case "replace_in_file":
    case "apply_patch":
    case "apply_unified_diff":
      return {
        pre: "Edited ",
        preKey: "humanize.edited",
        obj: a.path ? baseName(String(a.path)) : "files",
        ...(a.path ? {} : { objKey: "humanize.files" }),
      };
    case "grep":
      return {
        pre: "Searched the code for ",
        preKey: "humanize.searched",
        obj: `“${trunc(String(a.pattern ?? ""), 40)}”`,
      };
    case "git_log":
      return { pre: "Looked through recent git history", preKey: "humanize.gitLog" };
    case "todo_write": {
      // `todos` is current; `items` renders histories from before the rename (the old
      // key breaks Together's GLM-5.2 chat template — see src/delta/tools/todo.py).
      const items = Array.isArray(a.todos) ? a.todos : Array.isArray(a.items) ? a.items : [];
      if (items.length === 1) {
        const it = items[0] || {};
        const status = String(it.status || "").replace(/_/g, " ");
        return {
          pre: "Updated the plan — ",
          preKey: "humanize.updatedPlan",
          obj: `“${trunc(String(it.content ?? ""), 70)}”`,
          ...(status ? { post: ` → ${status}` } : {}),
        };
      }
      return {
        pre: `Updated the plan — ${items.length} items`,
        preKey: "humanize.planN",
        preVars: { n: items.length },
      };
    }
    case "send_message": {
      const { platform, tail } = messageTarget(String(a.target ?? ""));
      if (!tail) return { pre: "Sent a message", preKey: "humanize.sentMessage" };
      return {
        pre: `Sent a ${platform} message to `,
        preKey: "humanize.sentMessageTo",
        preVars: { platform },
        obj: tail,
      };
    }
    case "web_search":
      return {
        pre: "Searched the web — ",
        preKey: "humanize.webSearch",
        obj: `“${trunc(String(a.query ?? ""), 60)}”`,
      };
    case "web_fetch": {
      let host = String(a.url ?? "");
      try {
        host = new URL(host).host || host;
      } catch {
        /* keep raw */
      }
      return { pre: "Read a web page — ", preKey: "humanize.webFetch", obj: trunc(host, 50) };
    }
    case "explore":
      return {
        pre: "Sent a sub-agent to explore — ",
        preKey: "humanize.explored",
        obj: `“${trunc(String(a.task ?? a.prompt ?? ""), 60)}”`,
      };
    case "load_skill":
      // SKILLS-SPEC §4.1 #4 — the trust line: the transcript always shows the moment a
      // skill's instructions were picked up, model-invoked or forced via /skill.
      return { pre: "Used skill: ", preKey: "humanize.usedSkill", obj: String(a.name ?? "") };
    case "ask_user":
      return { pre: "Asked you a question", preKey: "humanize.askedQuestion" };
    case "propose_plan":
      return { pre: "Proposed a plan", preKey: "humanize.proposedPlan" };
    case "request_directory":
      return {
        pre: "Asked for folder access — ",
        preKey: "humanize.dirReq",
        obj: String(a.path ?? ""),
      };
    default: {
      const rest = trunc(shortArgs(a), 80);
      return {
        pre: `Used ${name}`,
        preKey: "humanize.usedTool",
        preVars: { name },
        ...(rest ? { post: ` — ${rest}` } : {}),
      };
    }
  }
}

// The approval card's headline (§35): the ask, phrased as the action being decided.
// run_shell leads with the model's own description ("Run a command — fetch stock data").
export function humanizeApprovalTitle(name: string, args: any): HumanLine {
  const a = args && typeof args === "object" ? args : {};
  switch (name) {
    case "write_file":
      return { pre: "Write ", preKey: "humanize.apWrite", obj: baseName(String(a.path ?? "a file")) };
    case "replace_in_file":
    case "apply_patch":
    case "apply_unified_diff":
      return {
        pre: "Edit ",
        preKey: "humanize.apEdit",
        obj: a.path ? baseName(String(a.path)) : "files",
        ...(a.path ? {} : { objKey: "humanize.files" }),
      };
    case "run_shell": {
      const desc = typeof a.description === "string" && a.description.trim() ? a.description.trim() : "";
      return {
        pre: "Run a command",
        preKey: "humanize.apRun",
        ...(desc ? { post: ` — ${desc.charAt(0).toLowerCase()}${desc.slice(1)}` } : {}),
      };
    }
    case "send_message": {
      const { tail } = messageTarget(String(a.target ?? ""));
      return tail
        ? { pre: "Send a message to ", preKey: "humanize.apSendTo", obj: tail }
        : { pre: "Send a message", preKey: "humanize.apSend" };
    }
    case "send_file": {
      const { tail } = messageTarget(String(a.target ?? ""));
      return tail
        ? { pre: "Send a file to ", preKey: "humanize.apSendFileTo", obj: tail }
        : { pre: "Send a file", preKey: "humanize.apSendFile" };
    }
    case "create_scheduled_task":
      return a.title
        ? { pre: "Create the automation ", preKey: "humanize.apTaskNamed", obj: `“${trunc(String(a.title), 60)}”` }
        : { pre: "Create an automation", preKey: "humanize.apTask" };
    case "save_skill":
      // SKILLS-SPEC §5.2/§7: "Add", never "install"; destination is "your skills".
      return a.name
        ? {
            pre: "Add skill ",
            preKey: "humanize.apSkillNamed",
            obj: String(a.name),
            post: " to your skills",
            postKey: "humanize.apToSkills",
          }
        : { pre: "Add a skill to your skills", preKey: "humanize.apSkill" };
    default:
      return { pre: `Use ${name}`, preKey: "humanize.apUse", preVars: { name } };
  }
}

// Approvals with no executed tool call (typically declined): the ask, phrased as intent.
export function humanizeAsk(name: string, args: any): HumanLine {
  const a = args && typeof args === "object" ? args : {};
  switch (name) {
    case "run_shell":
      return { pre: "Wanted to run ", preKey: "humanize.askRun", obj: trunc(String(a.command ?? ""), 60) };
    case "write_file":
      return { pre: "Wanted to write ", preKey: "humanize.askWrite", obj: baseName(String(a.path ?? "a file")) };
    case "replace_in_file":
    case "apply_patch":
    case "apply_unified_diff":
      return {
        pre: "Wanted to edit ",
        preKey: "humanize.askEdit",
        obj: a.path ? baseName(String(a.path)) : "files",
        ...(a.path ? {} : { objKey: "humanize.files" }),
      };
    case "send_message": {
      const { platform, tail } = messageTarget(String(a.target ?? ""));
      if (!tail) return { pre: "Wanted to send a message", preKey: "humanize.askSendMsg" };
      return {
        pre: `Wanted to message `,
        preKey: "humanize.askMsgTo",
        obj: tail,
        post: ` on ${platform}`,
        postKey: "humanize.askOn",
        postVars: { platform },
      };
    }
    default:
      return { pre: `Wanted to use ${name}`, preKey: "humanize.askUse", preVars: { name } };
  }
}
