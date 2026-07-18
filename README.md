# 🏇 Horse Race Arena

This is my magnum opus, my Mona Lisa and Starry Night, and I'm still painting it.

The idea starts with the fact that my friends love gambling. I'm not the sort,
because of my horrendous luck, but I still wanted to see what's up with it and
have fun with my friends, that's how this project came into existence.

This project is also my adventure into the full-stack development world, which
I had no experience in prior, so it's an opportunity to grow and learn while
having a lot of fun doing so.

> **Status: WIP 🚧** — v1 is fully playable end-to-end. Roadmap at the bottom.


https://github.com/user-attachments/assets/2e132759-53a6-4987-8e66-831a19215e02


A full-stack horse-race betting game. Races run on **Triomphe**, a custom
multi-agent A\* pathfinding engine I built first as a standalone simulation
project; players bet into a shared pool with live-moving odds, watch the race
replay on canvas, and get paid out pari-mutuel style, same as a real track.

**Play it live:** *(link coming, deploying to Render)*

---

## How it works

### The race engine ("Triomphe")
A variation of A\* pathfinding that accounts for what actually happens on a
racetrack. Each horse is an agent planning its route through a lane graph in
space *and* time: it reserves positions tick by tick, so two horses can never
occupy the same spot, and lane changes, blocking, and overtakes all emerge
from the planning. An overtake happens because the planner found a gap, not
because an animation said so. Stamina decay, current speed, and remaining
stamina all feed back into the planning too.

My favorite mechanic is how the reservations work. Horses plan in order of
position on the track, leaders first. At the start of every tick, each horse
gets a *synthetic* reservation: its position projected forward at constant
speed. As each horse plans, that estimate is swapped for its *real*,
tick-exact trajectory. And that's the whole trick: a horse in front only
ever sees synthetic guesses about the horses behind it, but it doesn't need
better, because they can't block it. A horse in the pack plans later, so
everyone ahead of it has already committed a real trajectory, and it has to
route around all of them precisely. The accuracy goes exactly where it
matters. The result is genuine blocking: a trailing horse boxed in behind a
slower leader has to wait for a gap or burn stamina swinging wide, exactly
like the real thing.

### Server-authoritative lifecycle
Every race moves through `betting → locked → revealed → settled`, driven by a
scheduler on the server:

- The race is simulated **only after betting closes**, and the result never
  leaves the server before then.
- Every race stores its RNG seed, drawn from OS entropy (`secrets`), so any
  past race can be re-run and verified -> provable fairness.
- Betting and payouts are handled in single database transactions, so money
  can't half-move: a bet either fully happens or fully doesn't. If the server
  crashes mid-race, a startup sweep voids the race and refunds everyone.

### Precompute-then-replay
The server simulates the race once and stores a slimmed-down position history
(~16 kB gzipped per race). Every client downloads that recording and replays
it on an HTML canvas, interpolating positions between samples, so the
playback is smooth regardless of screen refresh rate, and everyone watches
the exact same race in sync without any streaming.

### The odds (pari-mutuel betting)
This uses the **pari-mutuel** system, the one real horse tracks use. Instead
of the house promising you fixed odds, all bets go into a shared pool; when
the race ends, the house takes a cut (15% here) and the winners split what's
left, proportional to their stake. The "odds" on the board are just a live
projection of what the pool would currently pay.

Two things I changed from the textbook version to fit this game:

- **The house bets too, and it reads the race card.** A real track has
  thousands of bettors, so the pool prices itself. With a handful of players,
  an empty pool has no opinion, so the house seeds every race with phantom
  money. This is the real difference from plain pari-mutuel: the phantom
  money isn't spread evenly. A built-in handicapper (`estimate_strengths`)
  scores every horse on its speed, stamina, stamina drain, and starting lane
  (inner lanes run a shorter loop, and it matters a lot), then places the
  house's phantom stake proportionally. So the opening line already looks
  like a real race card, favorites cheap and longshots wild, before a single
  bet lands. At settlement the phantom stake participates like a real
  bettor's, which keeps the displayed odds and the actual payouts on the
  same formula.
- **Manipulation doesn't pay, by design.** An earlier version locked in each
  bet's odds at the moment you placed it, and that opened a hole: dump money
  on longshots to push the favorite's displayed odds up, then bet big on the
  favorite at that inflated locked-in price. Free money, every race. Instead
  of patching it, I switched to true pari-mutuel settlement, where the
  attack defeats itself: winners are paid out of the pool, so any money you
  pump in to distort the odds becomes part of the pot your own bet has to
  win back, diluted by your own stake. There is no locked-in price to abuse,
  because nothing is promised until the pool closes.

Longshot odds are uncapped on purpose. Yes, you'll occasionally see 900x on a
doomed horse in lane 7. And no, the house can't go broke, because your own
stake dilutes the payout the moment you bet.

### Tuning the odds (quant.py)
The handicapper's weights aren't guesses. `quant.py` runs the real engine a
thousand times and compares the handicapper's predicted win rates against
what actually happens, so the weights can be tuned until they agree.

```bash
python quant.py 1000
```

---

## Run it

```bash
pip install -r requirements.txt
uvicorn api:app
# open http://localhost:8000/static/
```

Races start automatically every ~90 seconds (30s betting + replay + pause).
Open two browser windows with different names to bet against yourself.

---

## Tech

- **Backend:** Python, FastAPI, asyncio, SQLite (raw SQL, no ORM, on purpose,
  to learn the transactions), Pydantic response models as the API contract
- **Frontend:** vanilla JS + HTML canvas, zero dependencies
- **Engine:** custom space-time A\* multi-agent planner (Python)

## What's on my mind next

- [ ] React migration (planned as its own learning chapter)
- [ ] A better odds model (extreme favorites are still slightly underpriced)
- [ ] WebSockets (replace polling)
- [ ] Rate limiting before public hosting
