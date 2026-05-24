#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
POLYMARKET COPY TRADING BOT v3.2
=================================
Fixes:
- /activity endpoint for real trades
- Transaction hash tracking
- Proper position deduplication
- Minimum trade size filter
- Strict cash control
"""

import asyncio
import aiohttp
import time
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Set
import logging
import sys

# ==================== CONFIG ====================
class Config:
    TEST_MODE = True
    INITIAL_CAPITAL = Decimal('50')
    TRADE_SIZE = Decimal('5')       # fixed $5 per trade
    MIN_CASH = Decimal('5')         # keep at least $5 cash
    SCAN_INTERVAL = 15              # seconds
    MIN_USDC_SIZE = Decimal('1')    # minimum trade size to copy ($1)

    DATA_API = "https://data-api.polymarket.com"
    GAMMA_API = "https://gamma-api.polymarket.com"

    TELEGRAM_TOKEN = "8616934680:AAHSkEqHzw2jhDyu431RXYsPkaWVCKxCbKc"
    TELEGRAM_CHAT_ID = "860803224"
    TRACKED_USERS = [
        {"name": "Oddn",             "wallet": "0xa53c26443fb636d8ae31ac24f62fc1d5ef8f67a5"},
        {"name": "Swisstony",        "wallet": "0x204f72f35326db932158cba6adff0b9a1da95e14"},
        {"name": "LaBradfordSmith22","wallet": "0x9495425feeb0c250accb89275c97587011b19a27"},
        {"name": "Mosley1",          "wallet": "0x5bec79df9add70a3892041ab1a5516b60f53b215"},
        {"name": "wan123",           "wallet": "0xde7be6d489bce070a959e0cb813128ae659b5f4b"},
        {"name": "Tiger200",         "wallet": "0x6211f97a76ed5c4b1d658f637041ac5f293db89e"},
    ]

# ==================== DATA MODELS ====================

@dataclass
class Position:
    position_id: str
    trader_name: str
    market_title: str
    market_slug: str
    side: str
    entry_price: Decimal
    size_usd: Decimal
    opened_at: datetime = field(default_factory=datetime.now)

@dataclass
class Portfolio:
    initial_capital: Decimal = field(default_factory=lambda: Config.INITIAL_CAPITAL)
    cash: Decimal = field(default_factory=lambda: Config.INITIAL_CAPITAL)
    realized_pnl: Decimal = field(default_factory=Decimal)
    open_positions: Dict[str, Position] = field(default_factory=dict)
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0

    @property
    def open_value(self) -> Decimal:
        return sum(p.size_usd for p in self.open_positions.values())

    @property
    def total_value(self) -> Decimal:
        return self.cash + self.open_value

    @property
    def total_pnl(self) -> Decimal:
        return self.total_value - self.initial_capital

    @property
    def pnl_percent(self) -> Decimal:
        if self.initial_capital == 0:
            return Decimal('0')
        return (self.total_pnl / self.initial_capital) * 100

# ==================== TELEGRAM ====================
class TelegramNotifier:
    def __init__(self):
        self.token = Config.TELEGRAM_TOKEN
        self.chat_id = Config.TELEGRAM_CHAT_ID
        self.session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def send(self, message: str):
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": message, "parse_mode": "Markdown"}
        try:
            async with self.session.post(url, json=payload) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logging.warning(f"Telegram error: {resp.status} - {text}")
        except Exception as e:
            logging.error(f"Telegram error: {e}")

    async def send_open(self, pos: Position, portfolio: Portfolio):
        pnl_emoji = "+" if portfolio.total_pnl >= 0 else "-"
        msg = (
            f"[POSITION OPENED]\n\n"
            f"Trader: *{pos.trader_name}*\n"
            f"Market: {pos.market_title[:50]}\n"
            f"Side: *{pos.side}*\n"
            f"Entry: ${pos.entry_price:.3f}\n"
            f"Size: ${pos.size_usd:.2f}\n"
            f"Time: {pos.opened_at.strftime('%H:%M:%S')}\n\n"
            f"--- PORTFOLIO ---\n"
            f"Cash: ${portfolio.cash:.2f}\n"
            f"Open: ${portfolio.open_value:.2f} ({len(portfolio.open_positions)} positions)\n"
            f"Total: ${portfolio.total_value:.2f}\n"
            f"PnL: ${portfolio.total_pnl:.2f} ({pnl_emoji}{portfolio.pnl_percent:.1f}%)"
        )
        await self.send(msg)

    async def send_close(self, pos: Position, pnl: Decimal, close_price: Decimal, portfolio: Portfolio):
        pnl_emoji = "+" if pnl >= 0 else "-"
        port_emoji = "+" if portfolio.total_pnl >= 0 else "-"
        win_rate = (portfolio.winning_trades / portfolio.total_trades * 100) if portfolio.total_trades > 0 else 0
        msg = (
            f"[POSITION CLOSED]\n\n"
            f"Trader: *{pos.trader_name}*\n"
            f"Market: {pos.market_title[:50]}\n"
            f"Side: *{pos.side}*\n"
            f"Entry: ${pos.entry_price:.3f}\n"
            f"Exit: ${close_price:.3f}\n"
            f"Size: ${pos.size_usd:.2f}\n"
            f"Trade PnL: {pnl_emoji}${pnl:.2f}\n"
            f"Time: {datetime.now().strftime('%H:%M:%S')}\n\n"
            f"--- PORTFOLIO ---\n"
            f"Cash: ${portfolio.cash:.2f}\n"
            f"Open: ${portfolio.open_value:.2f}\n"
            f"Total: ${portfolio.total_value:.2f}\n"
            f"Total PnL: {port_emoji}${portfolio.total_pnl:.2f} ({port_emoji}{portfolio.pnl_percent:.1f}%)\n"
            f"Win Rate: {win_rate:.0f}% ({portfolio.winning_trades}W/{portfolio.losing_trades}L)"
        )
        await self.send(msg)

    async def send_portfolio(self, portfolio: Portfolio):
        pnl_emoji = "+" if portfolio.total_pnl >= 0 else "-"
        win_rate = (portfolio.winning_trades / portfolio.total_trades * 100) if portfolio.total_trades > 0 else 0
        lines = [
            f"[PORTFOLIO STATUS]\n",
            f"Initial: ${portfolio.initial_capital:.2f}",
            f"Cash: ${portfolio.cash:.2f}",
            f"Open: ${portfolio.open_value:.2f} ({len(portfolio.open_positions)} positions)",
            f"Total: ${portfolio.total_value:.2f}",
            f"PnL: {pnl_emoji}${portfolio.total_pnl:.2f} ({pnl_emoji}{portfolio.pnl_percent:.1f}%)",
            f"Win Rate: {win_rate:.0f}% ({portfolio.winning_trades}W/{portfolio.losing_trades}L)\n",
        ]
        if portfolio.open_positions:
            lines.append("--- OPEN POSITIONS ---")
            for pos in portfolio.open_positions.values():
                lines.append(f"- {pos.trader_name} | {pos.side} | ${pos.size_usd:.2f} | {pos.market_title[:25]}")
        else:
            lines.append("No open positions")
        lines.append(f"\nTime: {datetime.now().strftime('%H:%M:%S')}")
        await self.send("\n".join(lines))

    async def send_no_cash(self, portfolio: Portfolio):
        await self.send(
            f"[INSUFFICIENT CASH]\n\n"
            f"Cash: ${portfolio.cash:.2f} - cannot open new position.\n"
            f"Waiting for positions to close...\n\n"
            f"Total: ${portfolio.total_value:.2f}"
        )

# ==================== USER TRACKER ====================
class UserTracker:
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.last_request = 0
        # Transaction hash'leri takip et
        self.seen_tx: Dict[str, Set[str]] = {u["wallet"]: set() for u in Config.TRACKED_USERS}
        # Aktif pozisyonlar (conditionId + outcomeIndex -> tx_hash)
        self.active_positions: Dict[str, Dict[str, str]] = {u["wallet"]: {} for u in Config.TRACKED_USERS}
        self.initialized: Dict[str, bool] = {u["wallet"]: False for u in Config.TRACKED_USERS}

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def _request(self, url: str):
        now = time.time()
        if now - self.last_request < 0.5:
            await asyncio.sleep(0.5 - (now - self.last_request))
        self.last_request = time.time()
        try:
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    return await resp.json()
                logging.warning(f"API error: {resp.status} - {url}")
                return None
        except Exception as e:
            logging.debug(f"Request error: {e}")
            return None

    def _make_pos_key(self, condition_id: str, outcome_index: int) -> str:
        return f"{condition_id}_{outcome_index}"

    async def get_new_trades(self, user: dict) -> List[dict]:
        """
        /activity endpoint'inden yeni trade'leri cek.
        Sadece TRADE tipindekiler ve usdcSize >= MIN_USDC_SIZE olanlar.
        """
        url = f"{Config.DATA_API}/activity?user={user['wallet']}&limit=50&type=TRADE"
        data = await self._request(url)

        if not data or not isinstance(data, list):
            return []

        wallet = user["wallet"]
        new_trades = []

        for activity in data:
            tx_hash = activity.get("transactionHash", "")
            if not tx_hash:
                continue

            # Ilk taramada sadece kaydet
            if not self.initialized[wallet]:
                self.seen_tx[wallet].add(tx_hash)
                continue

            # Daha once gorduk mu?
            if tx_hash in self.seen_tx[wallet]:
                continue

            self.seen_tx[wallet].add(tx_hash)

            # Min trade size kontrolu
            usdc_size_raw = activity.get("usdcSize", "0")
            try:
                usdc_size = Decimal(str(usdc_size_raw))
            except:
                usdc_size = Decimal("0")

            if usdc_size < Config.MIN_USDC_SIZE:
                logging.debug(f"Trade too small (${usdc_size:.2f}), skipping")
                continue

            activity["tracked_user"] = user["name"]
            activity["tracked_wallet"] = wallet
            new_trades.append(activity)

        if not self.initialized[wallet]:
            self.initialized[wallet] = True
            logging.info(f"Initialized {user['name']}: {len(self.seen_tx[wallet])} trades cached")

        return new_trades

    async def scan_all(self) -> dict:
        all_trades = []

        for user in Config.TRACKED_USERS:
            trades = await self.get_new_trades(user)
            all_trades.extend(trades)

        return {"trades": all_trades}

# ==================== MAIN BOT ====================
class PolymarketCopyBot:
    def __init__(self):
        self.tracker = UserTracker()
        self.notifier = TelegramNotifier()
        self.portfolio = Portfolio()
        self.running = False
        self.scan_count = 0
        self.no_cash_notified = False

    def _position_id(self, wallet: str, condition_id: str, outcome_index: int) -> str:
        side = "YES" if outcome_index == 1 else "NO"
        return f"{wallet[:8]}_{str(condition_id)[:30]}_{side}"

    def _get_side_from_outcome(self, outcome_index: int) -> str:
        return "YES" if outcome_index == 1 else "NO"

    def _is_open_trade(self, activity: dict) -> bool:
        """BUY = yeni pozisyon acma, SELL = kapatma"""
        side = activity.get("side", "").upper()
        return side == "BUY"

    def _is_close_trade(self, activity: dict) -> bool:
        side = activity.get("side", "").upper()
        return side == "SELL"

    async def open_position(self, activity: dict, notifier: TelegramNotifier):
        wallet = activity.get("tracked_wallet", "")
        condition_id = activity.get("conditionId", "")
        outcome_index = activity.get("outcomeIndex", 1)
        pos_id = self._position_id(wallet, condition_id, outcome_index)

        # Ayni pozisyon zaten aciksa atla
        if pos_id in self.portfolio.open_positions:
            logging.debug(f"Position already open, skipped: {pos_id}")
            return

        # Kesin nakit kontrolu
        if self.portfolio.cash < Config.TRADE_SIZE:
            if not self.no_cash_notified:
                await notifier.send_no_cash(self.portfolio)
                self.no_cash_notified = True
            logging.info(f"Insufficient cash (${self.portfolio.cash:.2f}), position skipped")
            return

        self.no_cash_notified = False

        side = self._get_side_from_outcome(outcome_index)
        trader_name = activity.get("tracked_user", "Unknown")

        price_raw = activity.get("price", "0.5")
        try:
            price = Decimal(str(price_raw))
            price = min(max(price, Decimal("0.01")), Decimal("0.99"))
        except:
            price = Decimal("0.5")

        title = activity.get("title", activity.get("question", "Unknown"))

        pos = Position(
            position_id=pos_id,
            trader_name=trader_name,
            market_title=str(title)[:60],
            market_slug=str(condition_id)[:30],
            side=side,
            entry_price=price,
            size_usd=Config.TRADE_SIZE,
        )

        self.portfolio.open_positions[pos_id] = pos
        self.portfolio.cash -= Config.TRADE_SIZE

        logging.info(f"OPENED: {trader_name} | {side} | ${Config.TRADE_SIZE} | Cash: ${self.portfolio.cash:.2f}")
        await notifier.send_open(pos, self.portfolio)

    async def close_position(self, activity: dict, notifier: TelegramNotifier):
        wallet = activity.get("tracked_wallet", "")
        condition_id = activity.get("conditionId", "")
        outcome_index = activity.get("outcomeIndex", 1)
        pos_id = self._position_id(wallet, condition_id, outcome_index)

        if pos_id not in self.portfolio.open_positions:
            logging.info(f"Position not found for closing: {pos_id[:30]}...")
            return

        pos = self.portfolio.open_positions[pos_id]

        # Cikis fiyati
        price_raw = activity.get("price", "0.5")
        try:
            close_price = Decimal(str(price_raw))
            close_price = min(max(close_price, Decimal("0.01")), Decimal("0.99"))
        except:
            close_price = pos.entry_price

        # PnL hesapla
        shares = pos.size_usd / pos.entry_price
        pnl = shares * (close_price - pos.entry_price)

        # Portfoy guncelle
        self.portfolio.cash += pos.size_usd + pnl
        self.portfolio.realized_pnl += pnl
        self.portfolio.total_trades += 1

        if pnl >= 0:
            self.portfolio.winning_trades += 1
        else:
            self.portfolio.losing_trades += 1

        del self.portfolio.open_positions[pos_id]
        logging.info(f"CLOSED: {pos.trader_name} | PnL: ${pnl:.2f} | Cash: ${self.portfolio.cash:.2f}")

        await notifier.send_close(pos, pnl, close_price, self.portfolio)

    async def scan_cycle(self):
        self.scan_count += 1
        async with self.tracker, self.notifier:
            result = await self.tracker.scan_all()

            for activity in result["trades"]:
                if self._is_open_trade(activity):
                    await self.open_position(activity, self.notifier)
                elif self._is_close_trade(activity):
                    await self.close_position(activity, self.notifier)

            if self.scan_count % 16 == 0:
                await self.notifier.send_portfolio(self.portfolio)
                logging.info(
                    f"Scan #{self.scan_count} | "
                    f"Total: ${self.portfolio.total_value:.2f} | "
                    f"PnL: ${portfolio.total_pnl:.2f} | "
                    f"Open: {len(self.portfolio.open_positions)}"
                )

    async def run(self):
        self.running = True
        async with self.notifier:
            await self.notifier.send(
                "[POLYMARKET COPY BOT v3.2 STARTED]\n\n"
                f"Capital: ${Config.INITIAL_CAPITAL}\n"
                f"Trade Size: ${Config.TRADE_SIZE} (fixed)\n"
                f"Min Cash: ${Config.MIN_CASH}\n"
                f"Min Trade: ${Config.MIN_USDC_SIZE}\n"
                f"Scan Interval: {Config.SCAN_INTERVAL} seconds\n"
                f"Tracking: {len(Config.TRACKED_USERS)} traders\n\n"
                "Tracking List:\n" +
                "\n".join(f"- {u['name']}" for u in Config.TRACKED_USERS) +
                "\n\nFixes:\n"
                "- /activity endpoint for real trades\n"
                "- Transaction hash deduplication\n"
                "- Min trade size filter\n"
                "- Strict cash control"
            )

        logging.info("=" * 50)
        logging.info("POLYMARKET COPY BOT v3.2 STARTED")
        logging.info("=" * 50)

        while self.running:
            try:
                await self.scan_cycle()
                await asyncio.sleep(Config.SCAN_INTERVAL)
            except KeyboardInterrupt:
                self.running = False
                async with self.notifier:
                    await self.notifier.send_portfolio(self.portfolio)
                    await self.notifier.send(
                        f"[BOT STOPPED]\n\n"
                        f"Total Scans: {self.scan_count}\n"
                        f"Final Capital: ${self.portfolio.total_value:.2f}\n"
                        f"Total PnL: ${portfolio.total_pnl:.2f}\n"
                        f"Trades: {self.portfolio.total_trades} "
                        f"({self.portfolio.winning_trades}W / {self.portfolio.losing_trades}L)"
                    )
                break
            except Exception as e:
                logging.error(f"Error: {e}")
                await asyncio.sleep(Config.SCAN_INTERVAL * 2)

# ==================== RUN ====================
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(message)s',
        handlers=[logging.StreamHandler(sys.stdout)]
    )
    bot = PolymarketCopyBot()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        pass
