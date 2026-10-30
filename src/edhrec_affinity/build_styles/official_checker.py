"""Read actual EDHPowerLevel assessments in its public browser application.

The page, including its own combo service, performs the assessment. No local
card classifier is used to manufacture an official observation. A missing
category, incomplete card import or combo-service failure fails closed.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import math
import re
import threading
import time
from urllib.parse import urlencode, urlparse

from .external_validation import cardlist_hash, record_external_validation, validation_record_key
from .sources import card_key

_SOURCE = "edhpowerlevel.com"
_BACKEND = "getcards-4zjvieuafa-uc.a.run.app"
_CATEGORIES = {
    "Extra Turns": "extra_turns", "Mass Land Denial": "mass_land_denial",
    "Early 2-Card Combos": "early_combos", "Late 2-Card Combos": "late_combos",
    "Game Changers": "game_changers",
}


def _retry_delay(value) -> float:
    if not isinstance(value, str):
        return 0
    try:
        delay = float(value)
        return max(0, delay) if math.isfinite(delay) else 0
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            return max(0, (date - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return 0

# Read the application's finished state, rather than an earlier page's DOM or
# an unexpanded accordion's visible text. We never modify its React state.
_SNAPSHOT_JS = r"""() => {
  let state = null;
  for (const el of document.querySelectorAll('*')) {
    const key = Object.keys(el).find(k => k.startsWith('__reactFiber$'));
    if (!key) continue;
    for (let fiber = el[key]; fiber; fiber = fiber.return) {
      const s = fiber.stateNode?.state;
      if (s && Array.isArray(s.formattedList) && 'avgPlayability' in s) {state=s; break;}
    }
    if (state) break;
  }
  const tracker = document.querySelector('.bracket-tracker');
  const details = document.querySelector('.bracket-details');
  const groups = [];
  for (const strong of tracker?.querySelectorAll(':scope > strong') || []) {
    const ul = strong.nextElementSibling;
    groups.push({heading:strong.textContent, items: ul?.tagName === 'UL'
      ? [...ul.children].map(li => ({text:li.textContent, href:li.querySelector('a')?.href || null})) : null});
  }
  let chaining = [];
  for (const strong of details?.querySelectorAll(':scope > strong') || []) {
    if (!strong.textContent.includes('Restricted Extra Turn Cards')) continue;
    let ul=strong.nextElementSibling;
    while (ul && ul.tagName !== 'UL' && ul.tagName !== 'STRONG') ul=ul.nextElementSibling;
    chaining=ul?.tagName === 'UL' ? [...ul.children].map(li => li.textContent) : null;
  }
  if (details?.textContent.includes('Restricted Extra Turn Cards') && (!chaining || !chaining.length)) chaining=null;
  return {
    state:state ? {cards:state.formattedList.map(c => ({name:c.name, quantity:c.quantity,
      matched:c.matched, flavor_name:c.flavor_name})), commanders:state.commander,
      loading:state.loading, toolError:state.toolError,
      combosComplete:Array.isArray(state.combos?.results?.included)} : null,
    groups, chaining, details:details?.textContent || '',
    tracker:tracker?.textContent || '',
    power:document.querySelector('.res-power-level .text-total')?.textContent,
    playability:document.querySelector('.res-playability .text-total')?.textContent,
    imported:document.body.textContent.match(/\b(\d+) total cards imported\./)?.[1]
  };
}"""


def deck_url(deck: dict) -> str:
    """The public preload format preserves the command-zone roles."""
    validation_record_key(deck)  # Check exact 100 cards and commander roles.
    commanders = {card_key(c['name']) for c in deck['commanders']}
    def line(card):
        name = re.sub(r"\s*(/+)\s*", r" \1 ", card['name'])
        return f"{card.get('quantity', 1)} {name}"
    main = [line(c) for c in deck['cards'] if card_key(c['name']) not in commanders]
    command = [line(c) for c in deck['cards'] if card_key(c['name']) in commanders]
    return "https://edhpowerlevel.com/?" + urlencode({'d':'~'.join(main) + '~~' + '~'.join(command) + '~Z~'})


def parse_observation(deck: dict, snapshot: dict, *, source_url: str, script_sha256: str, checked_at: str) -> dict:
    """Validate a complete page capture; return values for the atomic store."""
    validation_record_key(deck)
    state = snapshot.get('state') or {}
    if (state.get('loading') != 'hidden' or state.get('toolError') is not False
            or state.get('combosComplete') is not True or snapshot.get('imported') != '100'):
        raise ValueError('EDHPowerLevel card or combo analysis is incomplete')
    wanted = {card_key(c['name']): c['name'] for c in deck['cards']}
    # A known flavor alias can be returned under its Oracle name. Match exactly
    # through the site's own flavor_name field, never by fuzzy string matching.
    aliases = {}
    actual = []
    for card in state.get('cards', []):
        if card.get('matched') is not True:
            raise ValueError('EDHPowerLevel did not resolve every imported card')
        flavor_names = card.get('flavor_name') or []
        if isinstance(flavor_names, str):
            flavor_names = [flavor_names]
        if not isinstance(flavor_names, list) or any(not isinstance(n, str) for n in flavor_names):
            raise ValueError('EDHPowerLevel returned an unreadable card alias')
        names = [card_key(n) for n in [card.get('name', ''), *flavor_names]]
        matches = [name for name in names if name in wanted]
        if len(set(matches)) != 1:
            raise ValueError(f"EDHPowerLevel imported a different card: {card.get('name')}")
        full_name = wanted[matches[0]]
        for name in names:
            if name: aliases[name] = full_name
        actual.append({'name':full_name, 'quantity':card.get('quantity')})
    if cardlist_hash(actual) != cardlist_hash(deck['cards']):
        raise ValueError('EDHPowerLevel imported different card quantities')
    def canonical(name):
        if not isinstance(name, str) or card_key(name.strip()) not in aliases:
            raise ValueError('EDHPowerLevel restriction names a card outside the checked deck')
        return aliases[card_key(name.strip())]
    if (not isinstance(state.get('commanders'), list)
            or Counter(card_key(canonical(n)) for n in state['commanders'])
            != Counter(card_key(c['name']) for c in deck['commanders'])):
        raise ValueError('EDHPowerLevel imported different commander roles')
    restrictions = {}
    for group in snapshot.get('groups', []):
        match = re.fullmatch(r'(.+?):\s*(\d+)\s*', group.get('heading', ''))
        if not match or match[1] not in _CATEGORIES or not isinstance(group.get('items'), list):
            raise ValueError('EDHPowerLevel requirement tracker format changed or reported an error')
        category = _CATEGORIES[match[1]]
        if category in restrictions or len(group['items']) != int(match[2]):
            raise ValueError('EDHPowerLevel requirement tracker is incomplete')
        if category.endswith('combos'):
            combos = []
            for item in group['items']:
                href = urlparse(item.get('href') or '')
                if href.hostname not in {'commanderspellbook.com', 'www.commanderspellbook.com'} or not href.path.startswith('/combo/'):
                    raise ValueError('EDHPowerLevel combo observation has no source link')
                names = [canonical(n) for n in item['text'].split(' + ')]
                if len(set(map(card_key, names))) < 2:
                    raise ValueError('EDHPowerLevel combo observation is incomplete')
                combos.append(list(dict.fromkeys(names)))
            restrictions[category] = combos
        else:
            restrictions[category] = list(dict.fromkeys(canonical(i['text']) for i in group['items']))
    if set(restrictions) != set(_CATEGORIES.values()) or not isinstance(snapshot.get('chaining'), list):
        raise ValueError('EDHPowerLevel did not expose every restriction category')
    restrictions['chaining_extra_turns'] = list(dict.fromkeys(canonical(n) for n in snapshot['chaining']))
    if not set(restrictions['chaining_extra_turns']).issubset(restrictions['extra_turns']):
        raise ValueError('EDHPowerLevel extra-turn tracker is inconsistent')
    bracket = re.search(r'Recommended Bracket:\s*([1-5])(?!\d)', snapshot.get('details', ''))
    power = snapshot.get('power', '')
    playability = snapshot.get('playability', '')
    if (not bracket or not isinstance(power, str) or not re.fullmatch(r'\d+(?:\.\d+)?\+?', power.strip())
            or not isinstance(playability, str) or not re.fullmatch(r'\d+(?:\.\d+)?%', playability.strip())):
        raise ValueError('EDHPowerLevel did not return a readable score and bracket')
    return {'source':_SOURCE, 'source_url':source_url, 'checked_at':checked_at,
            'card_count':100, 'power_level':power.strip(), 'playability_pct':float(playability.strip()[:-1]),
            'bracket':int(bracket[1]), 'bracket_restrictions':restrictions,
            'restriction_check':{'status':'complete', 'method':'edhpowerlevel_page',
                'source_script_sha256':script_sha256,
                'commander_names':[c['name'] for c in deck['commanders']]}}


class SharedCheckAdmission:
    """One request-start clock and source stop signal for every browser worker."""

    def __init__(self, *, min_interval_seconds=5, monotonic=time.monotonic, sleep=time.sleep):
        if not math.isfinite(min_interval_seconds) or min_interval_seconds < 0:
            raise ValueError('Check interval must be a finite nonnegative number')
        self.min_interval_seconds = max(5, min_interval_seconds)
        self._monotonic, self._sleep = monotonic, sleep
        self._lock = threading.Lock()
        self._next_start = None
        self._reason = None
        self._retry_after_seconds = 0

    @property
    def blocked(self):
        with self._lock:
            return self._reason is not None

    @property
    def reason(self):
        with self._lock:
            return self._reason

    @property
    def retry_after_seconds(self):
        with self._lock:
            return self._retry_after_seconds

    def stop(self, reason, retry_after_seconds=0):
        if type(retry_after_seconds) not in {int, float} or not math.isfinite(retry_after_seconds):
            retry_after_seconds = 0
        with self._lock:
            self._reason = self._reason or str(reason or 'EDHPowerLevel access stopped')
            self._retry_after_seconds = max(self._retry_after_seconds, retry_after_seconds)

    def acquire(self, *, timeout_seconds=120):
        """Admit one start, or return false on a shared block or expired budget.

        Only actual admissions advance the clock. Waiting workers poll briefly
        so a source block stops them without waiting out the entire interval.
        """
        deadline = self._monotonic() + max(0, timeout_seconds)
        while True:
            with self._lock:
                now = self._monotonic()
                if self._reason is not None or now >= deadline:
                    return False
                delay = max(0, (self._next_start or now) - now)
                if delay == 0:
                    self._next_start = now + self.min_interval_seconds
                    return True
                remaining = deadline - now
            self._sleep(min(delay, remaining, 0.1))


class OfficialDeckChecker:
    """One thread-owned browser session, with admission shared across workers."""
    def __init__(self, *, min_interval_seconds: float = 5, admission=None, admission_managed=False):
        self._admission = admission if admission is not None else SharedCheckAdmission(
            min_interval_seconds=min_interval_seconds)
        self.min_interval_seconds = self._admission.min_interval_seconds
        self._admission_managed = admission_managed
        self._browser = None
        self._playwright = None

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self._playwright = sync_playwright().start()
        try:
            self._browser = self._playwright.chromium.launch(headless=True)
            self._context = self._browser.new_context()
        except Exception:
            self._playwright.stop()
            self._playwright = None
            raise
        return self

    def __exit__(self, *_):
        try:
            if self._browser is not None:
                self._browser.close()
        finally:
            if self._playwright is not None:
                self._playwright.stop()
            self._browser = self._playwright = None

    def check(self, deck: dict, *, validation_directory, timeout_seconds: float = 120) -> dict:
        checked_at = datetime.now(timezone.utc).isoformat()
        result = {'status':'failed', 'source_blocked':False, 'checked_at':checked_at,
                  'deck_sha256':cardlist_hash(deck['cards'])}
        def blocked_result():
            return {**result, 'status':'blocked', 'source_blocked':True, 'reason':self._admission.reason,
                    'retry_after_seconds':self._admission.retry_after_seconds}
        page = None
        try:
            url = deck_url(deck)
            if self._admission.blocked:
                return blocked_result()
            if not self._admission_managed and not self._admission.acquire(timeout_seconds=timeout_seconds):
                return blocked_result() if self._admission.blocked else {
                    **result, 'reason':'EDHPowerLevel request admission exceeded the remaining time budget'}
            deadline = time.monotonic() + timeout_seconds
            page = self._context.new_page()
            scripts = {}
            backend_completed = []
            def response_seen(response):
                parsed = urlparse(response.url)
                if parsed.hostname in {_SOURCE, _BACKEND} and response.status in {401, 403, 429}:
                    self._admission.stop(
                        f'EDHPowerLevel returned HTTP {response.status}; the external validation queue stopped',
                        retry_after_seconds=_retry_delay(getattr(response, 'headers', {}).get('retry-after')))
                if parsed.hostname == _BACKEND and response.status == 200:
                    try:
                        payload = response.json()
                        backend_completed.append(isinstance(payload.get('data'), list)
                            and isinstance(payload.get('combo', {}).get('results', {}).get('included'), list))
                    except Exception:
                        backend_completed.append(False)
                if parsed.hostname == _SOURCE and re.fullmatch(r'/js/main-[\w-]+\.js', parsed.path):
                    try:
                        body = response.body()
                        if b'bracket-tracker' in body and b'Restricted Extra Turn Cards' in body:
                            scripts[response.url] = hashlib.sha256(body).hexdigest()
                    except Exception:
                        pass
            page.on('response', response_seen)
            if self._admission.blocked:
                return blocked_result()
            page.goto(url, wait_until='domcontentloaded', timeout=min(timeout_seconds * 1000, 60000))
            snapshot = None
            while time.monotonic() < deadline:
                if self._admission.blocked:
                    return blocked_result()
                snapshot = page.evaluate(_SNAPSHOT_JS)
                if backend_completed and snapshot.get('groups') and (snapshot.get('state') or {}).get('loading') == 'hidden':
                    break
                page.wait_for_timeout(250)
            if len(scripts) != 1:
                raise ValueError('Could not identify the official assessment script version')
            if not backend_completed or not all(backend_completed):
                raise ValueError('No complete fresh EDHPowerLevel card and combo service response was observed')
            if urlparse(page.url).hostname != _SOURCE:
                raise ValueError('EDHPowerLevel redirected outside the assessment site')
            values = parse_observation(deck, snapshot or {}, source_url=url,
                script_sha256=next(iter(scripts.values())),
                checked_at=datetime.now(timezone.utc).isoformat())
            if self._admission.blocked:
                return blocked_result()
            observation = record_external_validation(deck, values, validation_directory)
            return {**result, 'status':'verified', 'checked_at':observation['checked_at'], 'observation':observation}
        except Exception as error:
            if self._admission.blocked:
                return blocked_result()
            return {**result, 'reason':f'{type(error).__name__}: {error}'}
        finally:
            if page is not None:
                page.close()


def check_deck(deck: dict, *, validation_directory, timeout_seconds: float = 120, checker=None) -> dict:
    """Check and atomically store one actual, complete, exact-list assessment."""
    if checker is not None:
        return checker.check(deck, validation_directory=validation_directory, timeout_seconds=timeout_seconds)
    try:
        with OfficialDeckChecker() as session:
            return session.check(deck, validation_directory=validation_directory, timeout_seconds=timeout_seconds)
    except Exception as error:
        return {'status':'failed', 'source_blocked':False, 'reason':f'{type(error).__name__}: {error}',
                'checked_at':datetime.now(timezone.utc).isoformat(), 'deck_sha256':cardlist_hash(deck['cards'])}
