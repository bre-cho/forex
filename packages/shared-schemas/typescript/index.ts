/**
 * Shared TypeScript event type definitions for the Forex platform.
 * These types are auto-derived from packages/shared-schemas/contracts/events.json
 */

export interface BaseEvent {
  event_id: string;
  event_type: string;
  source_service: string;
  bot_instance_id?: string;
  workspace_id?: string;
  user_id?: string;
  timestamp: string; // ISO 8601
  payload: Record<string, unknown>;
  schema_version: string;
}

export interface BotStatusEvent extends BaseEvent {
  event_type: "bot.status_changed";
  payload: {
    status: "stopped" | "starting" | "running" | "paused" | "error";
    error_message?: string;
  };
}

export interface TradeEvent extends BaseEvent {
  event_type: "trade.executed" | "trade.closed";
  payload: {
    trade_id: string;
    symbol: string;
    side: "buy" | "sell";
    volume: number;
    entry_price: number;
    exit_price?: number;
    pnl?: number;
    commission?: number;
  };
}

export interface SignalEvent extends BaseEvent {
  event_type: "signal.generated";
  payload: {
    signal_id: string;
    symbol: string;
    direction: "buy" | "sell" | "close";
    confidence: number;
    wave_state: string;
    entry_price?: number;
    stop_loss?: number;
    take_profit?: number;
  };
}

export interface AccountUpdateEvent extends BaseEvent {
  event_type: "account.updated";
  payload: {
    balance: number;
    equity: number;
    margin: number;
    free_margin: number;
    daily_pnl: number;
  };
}

export interface NotificationEvent extends BaseEvent {
  event_type: "notification.sent";
  payload: {
    title: string;
    body: string;
    channel: "email" | "telegram" | "discord" | "webhook";
  };
}

export type PlatformEvent =
  | BotStatusEvent
  | TradeEvent
  | SignalEvent
  | AccountUpdateEvent
  | NotificationEvent;

export function isBotStatusEvent(e: BaseEvent): e is BotStatusEvent {
  return e.event_type === "bot.status_changed";
}

export function isTradeEvent(e: BaseEvent): e is TradeEvent {
  return e.event_type === "trade.executed" || e.event_type === "trade.closed";
}

export function isSignalEvent(e: BaseEvent): e is SignalEvent {
  return e.event_type === "signal.generated";
}
