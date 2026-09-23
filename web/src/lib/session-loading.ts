import type { Session } from "./types";

/** A blank server/session-list record can render immediately. Detail polling
 * still detects a turn started in another tab and loads it when needed. */
export function isBlankSession(session: Session | undefined): boolean {
  return session?.blank === true && session.messageCount === 0
    && !session.inFlight && !session.pendingApproval && !session.pendingQuestion;
}
