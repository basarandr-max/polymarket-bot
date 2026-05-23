#!/usr/bin/env python3
"""
POLYMARKET COPY TRADING BOT v2.0
=================================
Sermaye: $50 (TEST MODU - işlem yapılmaz)
Strateji: Trader Kopyalama
Tarama: 10 saniye
Bildirim: Telegram
"""

import asyncio
import aiohttp
import json
import time
import random
from decimal import Decimal
from datetime import datetime
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from enum import Enum
import logging
import sys

# ==================== KONFİGÜRASYON ====================
class Config:
    TEST_MODE = True
    INITIAL_CAPITAL = Decimal('50')

    # Her işlem için sermayenin yüzdesi
    TRADE_PERCENT = Decimal('0.10')  # %10 per trade = $5

    SCAN_INTERVAL = 10  # saniye

    DATA_API = "https://data-api.polymarket.com"
    GAMMA_API = "https://gamma-api.polymarket.com"

    TELEGRAM_TOKEN = "8616934680:AAHSkEqHzw2jhDyu431RXYsPkaWVCKxCbKc"
    TELEGRAM_CHAT_ID = "860803224"  # <-- KENDİ ID'Nİ YAZ

    TRACKED_USERS = [
        {"name": "Oddn",             "wallet": "0xa53c26443fb636d8ae31ac24f62fc1d5ef8f67a5"},
        {"name": "Swisstony",        "wallet": "0x204f72f35326db932158cba6adff0b9a1da95e14"},
        {"name": "LaBradfordSmith22","wallet": "0x9495425feeb0c250accb89275c97587011b19a27"},
        {"name": "Mosley1",          "wallet": "0x5bec79df9add70a3892041ab1a5516b60f53b215"},
        {"name": "wan123",           "wallet": "0xde7be6d489bce070a959e0cb813128ae659b5f4b"},
        {"name": "Tiger200",         "wallet": "0x6211f97a76ed5c4b1d658f637041ac5f293db89e"},
    ]

# ==================== VERİ MODELLERİ ====================

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
    initial_capital: Decimal = Config.INITIAL_CAPITAL
    cash: Decimal = Config.INITIAL_CAPITAL
    realized_pnl: Decimal = Decimal('0')
    open_positions: Dict[str, Position] = field(default_factory=dict)

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
                    logging.warning(f"Telegram hatasi: {resp.status} - {text}")
        except Exception as e:
            logging.error(f"Telegram hatasi: {e}")

    async def send_open(self, pos: Position, portfolio: Portfolio):
        pnl_emoji = "🟢" if portfolio.total_pnl >= 0 else "🔴"
        msg = (
            f"📂 *POZİSYON AÇILDI*\n\n"
            f"👤 Trader: *{pos.trader_name}*\n"
            f"🏟️ Pazar: {pos.market_title[:50]}\n"
            f"📊 Yön: *{pos.side}*\n"
            f"💰 Giriş Fiyatı: ${pos.entry_price:.3f}\n"
            f"💵 Yatırılan: ${pos.size_usd:.2f}\n"
            f"⏰ {pos.opened_at.strftime('%H:%M:%S')}\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💼 *PORTFÖY*\n"
            f"Nakit: ${portfolio.cash:.2f}\n"
            f"Açık: ${portfolio.open_value:.2f}\n"
            f"Toplam: ${portfolio.total_value:.2f}\n"
            f"{pnl_emoji} PnL: ${portfolio.total_pnl:.2f} (%{portfolio.pnl_percent:.1f})"
        )
        await self.send(msg)

    async def send_close(self, pos: Position, pnl: Decimal, close_price: Decimal, portfolio: Portfolio):
        pnl_emoji = "✅" if pnl >= 0 else "❌"
        port_emoji = "🟢" if portfolio.total_pnl >= 0 else "🔴"
        msg = (
            f"{pnl_emoji} *POZİSYON KAPATILDI*\n\n"
            f"👤 Trader: *{pos.trader_name}*\n"
            f"🏟️ Pazar: {pos.market_title[:50]}\n"
            f"📊 Yön: *{pos.side}*\n"
            f"📥 Giriş: ${pos.entry_price:.3f}\n"
            f"📤 Çıkış: ${close_price:.3f}\n"
            f"💵 Yatırılan: ${pos.size_usd:.2f}\n"
            f"{'📈' if pnl >= 0 else '📉'} İşlem PnL: ${pnl:.2f}\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💼 *PORTFÖY*\n"
            f"Nakit: ${portfolio.cash:.2f}\n"
            f"Açık: ${portfolio.open_value:.2f}\n"
            f"Toplam: ${portfolio.total_value:.2f}\n"
            f"{port_emoji} PnL: ${portfolio.total_pnl:.2f} (%{portfolio.pnl_percent:.1f})"
        )
        await self.send(msg)

    async def send_portfolio(self, portfolio: Portfolio):
        pnl_emoji = "🟢" if portfolio.total_pnl >= 0 else "🔴"
        lines = [
            f"📊 *PORTFÖY DURUMU*\n",
            f"💰 Başlangıç: ${portfolio.initial_capital:.2f}",
            f"💵 Nakit: ${portfolio.cash:.2f}",
            f"📂 Açık Pozisyonlar: ${portfolio.open_value:.2f}",
            f"💼 Toplam Değer: ${portfolio.total_value:.2f}",
            f"{pnl_emoji} Toplam PnL: ${portfolio.total_pnl:.2f} (%{portfolio.pnl_percent:.1f})\n",
        ]
        if portfolio.open_positions:
            lines.append("━━━━━━━━━━━━━━━━━━")
            lines.append("📂 *Açık Pozisyonlar:*")
            for pos in portfolio.open_positions.values():
                lines.append(f"• {pos.trader_name} | {pos.side} | ${pos.size_usd:.2f} | {pos.market_title[:30]}")
        else:
            lines.append("📭 Açık pozisyon yok")
        lines.append(f"\n⏰ {datetime.now().strftime('%H:%M:%S')}")
        await self.send("\n".join(lines))

# ==================== KULLANICI TAKİPÇİSİ ====================
class UserTracker:
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.last_request = 0
        self.seen_tx: Dict[str, set] = {u["wallet"]: set() for u in Config.TRACKED_USERS}
        self.user_positions: Dict[str, Dict[str, dict]] = {u["wallet"]: {} for u in Config.TRACKED_USERS}

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def _request(self, url: str) -> Optional[any]:
        now = time.time()
        if now - self.last_request < 0.5:
            await asyncio.sleep(0.5 - (now - self.last_request))
        self.last_request = time.time()
        try:
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    return await resp.json()
                return None
        except Exception as e:
            logging.debug(f"Request hatasi: {e}")
            return None

    async def get_new_activities(self, user: dict) -> List[dict]:
        url = f"{Config.DATA_API}/activity?user={user['wallet']}&limit=10"
        data = await self._request(url)
        if not data or not isinstance(data, list):
            return []
        new_activities = []
        for activity in data:
            tx_hash = activity.get("transactionHash", "")
            if tx_hash and tx_hash not in self.seen_tx[user["wallet"]]:
                self.seen_tx[user["wallet"]].add(tx_hash)
                activity["tracked_user"] = user["name"]
                activity["tracked_wallet"] = user["wallet"]
                new_activities.append(activity)
        return new_activities

    async def get_user_positions(self, user: dict) -> List[dict]:
        url = f"{Config.DATA_API}/positions?user={user['wallet']}"
        data = await self._request(url)
        if not data or not isinstance(data, list):
            return []
        return data

    async def detect_closes(self, user: dict) -> List[dict]:
        current_raw = await self.get_user_positions(user)
        current_ids = {p.get("conditionId", p.get("asset", "")): p for p in current_raw}
        prev_ids = self.user_positions[user["wallet"]]
        closed = []
        for cid, prev_pos in prev_ids.items():
            if cid not in current_ids:
                closed_item = dict(prev_pos)
                closed_item["tracked_user"] = user["name"]
                closed_item["tracked_wallet"] = user["wallet"]
                closed_item["event"] = "CLOSE"
                closed.append(closed_item)
        self.user_positions[user["wallet"]] = current_ids
        return closed

    async def scan_all(self) -> dict:
        opens = []
        closes = []
        for user in Config.TRACKED_USERS:
            new_acts = await self.get_new_activities(user)
            for act in new_acts:
                side = act.get("side", "").upper()
                if side in ("BUY", "YES", "NO", "SELL"):
                    opens.append(act)
            closed = await self.detect_closes(user)
            closes.extend(closed)
        return {"opens": opens, "closes": closes}

# ==================== ANA BOT ====================
class PolymarketCopyBot:
    def __init__(self):
        self.tracker = UserTracker()
        self.notifier = TelegramNotifier()
        self.portfolio = Portfolio()
        self.running = False
        self.scan_count = 0
        self.my_positions: Dict[str, Position] = {}

    def _trade_size(self) -> Decimal:
        size = self.portfolio.cash * Config.TRADE_PERCENT
        return max(Decimal('1'), min(size, self.portfolio.cash))

    def _position_id(self, activity: dict) -> str:
        wallet = activity.get("tracked_wallet", "")
        market = activity.get("conditionId", activity.get("market", activity.get("slug", "")))
        side = activity.get("side", "")
        return f"{wallet[:8]}_{market[:16]}_{side}"

    def _parse_activity(self, activity: dict) -> dict:
        side = activity.get("side", "BUY").upper()
        if side in ("BUY", "YES"):
            side = "YES"
        elif side in ("SELL", "NO"):
            side = "NO"
        price_raw = activity.get("price", activity.get("avgPrice", "0.5"))
        try:
            price = Decimal(str(price_raw))
        except:
            price = Decimal("0.5")
        title = activity.get("title", activity.get("question", activity.get("market", "Bilinmiyor")))
        slug = activity.get("slug", activity.get("conditionId", ""))
        return {"side": side, "price": price, "title": str(title)[:60], "slug": str(slug)}

    async def open_position(self, activity: dict, notifier: TelegramNotifier):
        pos_id = self._position_id(activity)
        if pos_id in self.my_positions:
            return
        size = self._trade_size()
        if size < Decimal('1'):
            logging.info("Yeterli nakit yok, pozisyon atlandı")
            return
        parsed = self._parse_activity(activity)
        trader_name = activity.get("tracked_user", "Bilinmiyor")
        pos = Position(
            position_id=pos_id,
            trader_name=trader_name,
            market_title=parsed["title"],
            market_slug=parsed["slug"],
            side=parsed["side"],
            entry_price=parsed["price"],
            size_usd=size,
        )
        self.my_positions[pos_id] = pos
        self.portfolio.open_positions[pos_id] = pos
        self.portfolio.cash -= size
        logging.info(f"POZİSYON AÇILDI: {trader_name} | {parsed['side']} | ${size:.2f}")
        await notifier.send_open(pos, self.portfolio)

    async def close_position(self, activity: dict, notifier: TelegramNotifier):
        pos_id = self._position_id(activity)
        if pos_id not in self.my_positions:
            return
        pos = self.my_positions[pos_id]
        price_raw = activity.get("price", activity.get("avgPrice", None))
        if price_raw:
            try:
                close_price = Decimal(str(price_raw))
            except:
                close_price = Decimal("0.5")
        else:
            close_price = pos.entry_price * Decimal(str(random.uniform(0.7, 1.4)))

        if pos.side == "YES":
            pnl = pos.size_usd * ((close_price - pos.entry_price) / pos.entry_price)
        else:
            pnl = pos.size_usd * ((pos.entry_price - close_price) / pos.entry_price)

        self.portfolio.cash += pos.size_usd + pnl
        self.portfolio.realized_pnl += pnl
        del self.my_positions[pos_id]
        del self.portfolio.open_positions[pos_id]
        logging.info(f"POZİSYON KAPATILDI: {pos.trader_name} | PnL: ${pnl:.2f}")
        await notifier.send_close(pos, pnl, close_price, self.portfolio)

    async def scan_cycle(self):
        self.scan_count += 1
        async with self.tracker, self.notifier:
            result = await self.tracker.scan_all()
            for activity in result["opens"]:
                await self.open_position(activity, self.notifier)
            for activity in result["closes"]:
                await self.close_position(activity, self.notifier)
            if self.scan_count % 6 == 0:
                await self.notifier.send_portfolio(self.portfolio)
                logging.info(
                    f"Tarama #{self.scan_count} | "
                    f"Toplam: ${self.portfolio.total_value:.2f} | "
                    f"PnL: ${self.portfolio.total_pnl:.2f}"
                )

    async def run(self):
        self.running = True
        async with self.notifier:
            await self.notifier.send(
                "🤖 *POLYMARKET COPY BOT v2.0 BAŞLADI*\n\n"
                f"💰 Sermaye: ${Config.INITIAL_CAPITAL}\n"
                f"📊 Mod: TEST (işlem yapılmıyor)\n"
                f"🎯 Strateji: Trader Kopyalama\n"
                f"💵 İşlem Büyüklüğü: %{Config.TRADE_PERCENT * 100:.0f} (her işlemde)\n"
                f"⏰ Tarama: {Config.SCAN_INTERVAL} saniye\n"
                f"👥 Takip edilen: {len(Config.TRACKED_USERS)} trader\n\n"
                "👥 *Takip listesi:*\n" +
                "\n".join(f"• {u['name']}" for u in Config.TRACKED_USERS) +
                "\n\n⚠️ Test modu: gerçek işlem yapılmaz"
            )

        logging.info("=" * 50)
        logging.info("POLYMARKET COPY BOT BASLADI")
        logging.info(f"Sermaye: ${Config.INITIAL_CAPITAL}")
        logging.info(f"Takip: {len(Config.TRACKED_USERS)} trader")
        logging.info("=" * 50)

        while self.running:
            try:
                await self.scan_cycle()
                await asyncio.sleep(Config.SCAN_INTERVAL)
            except KeyboardInterrupt:
                self.running = False
                logging.info("Bot durduruluyor...")
                async with self.notifier:
                    await self.notifier.send_portfolio(self.portfolio)
                    await self.notifier.send(
                        f"🛑 *BOT DURDURULDU*\n\n"
                        f"Toplam Tarama: {self.scan_count}\n"
                        f"Son Sermaye: ${self.portfolio.total_value:.2f}\n"
                        f"Toplam PnL: ${self.portfolio.total_pnl:.2f}"
                    )
                break
            except Exception as e:
                logging.error(f"Hata: {e}")
                await asyncio.sleep(Config.SCAN_INTERVAL * 2)

# ==================== ÇALIŞTIRMA ====================
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
