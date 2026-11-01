// Ctrl/Cmd+K command palette (cmdk). "Needs you" items first, then commands.
import { Command } from "cmdk";
import type { PlanTask, Run } from "./api";
import { LaneBadge } from "./components";

export interface PaletteActions {
  approve: (id: string) => void;
  reject: (id: string) => void;
  escalate: (id: string) => void;
  runTask: (task: PlanTask) => void;
  focusComposer: () => void;
  openBrief: () => void;
  runQueueNow: () => void;
}

export function Palette({
  open,
  onOpenChange,
  inbox,
  nextTask,
  actions,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  inbox: Run[];
  nextTask: PlanTask | null;
  actions: PaletteActions;
}) {
  const close = (fn: () => void) => () => {
    fn();
    onOpenChange(false);
  };
  return (
    <Command.Dialog
      open={open}
      onOpenChange={onOpenChange}
      label="Command palette"
      className="palette"
      overlayClassName="palette-overlay"
    >
      <Command.Input placeholder="Search or run a command" className="palette-input" />
      <Command.List className="palette-list">
        <Command.Empty className="palette-empty">Nothing matches.</Command.Empty>
        {inbox.length > 0 && (
          <Command.Group heading="Needs you">
            {inbox.map((run) => (
              <Command.Item
                key={run.id}
                value={`approve ${run.task} ${run.id}`}
                onSelect={close(() => actions.approve(run.id))}
              >
                <LaneBadge lane={run.lane} />
                <span className="grow">Approve: {run.task}</span>
              </Command.Item>
            ))}
            {inbox.map((run) => (
              <Command.Item
                key={`esc-${run.id}`}
                value={`escalate ${run.task} ${run.id}`}
                onSelect={close(() => actions.escalate(run.id))}
              >
                <span className="escalate-mark" aria-hidden="true" />
                <span className="grow">Escalate to Claude: {run.task}</span>
              </Command.Item>
            ))}
          </Command.Group>
        )}
        <Command.Group heading="Commands">
          {nextTask && (
            <Command.Item value={`run next task ${nextTask.title}`} onSelect={close(() => actions.runTask(nextTask))}>
              <span className="grow">
                Run next task · {nextTask.id} {nextTask.title}
              </span>
            </Command.Item>
          )}
          <Command.Item value="new task message the team" onSelect={close(actions.focusComposer)}>
            <span className="grow">New task</span>
            <kbd>N</kbd>
          </Command.Item>
          <Command.Item value="open morning brief overnight" onSelect={close(actions.openBrief)}>
            <span className="grow">Open morning brief</span>
            <kbd>G M</kbd>
          </Command.Item>
          <Command.Item value="run tonight queue now overnight" onSelect={close(actions.runQueueNow)}>
            <span className="grow">Run tonight's queue now</span>
          </Command.Item>
        </Command.Group>
      </Command.List>
      <div className="palette-foot mono">
        <span>↑↓ move</span>
        <span>↵ run</span>
        <span className="grow" />
        <span>All local · no network</span>
      </div>
    </Command.Dialog>
  );
}
