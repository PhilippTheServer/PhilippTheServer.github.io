---
layout: post
title: "I Reverse-Engineered My Smart Ring BLE Protocol Clean-Room"
subtitle: "COLMi R02 on a Realtek RTL8762E, read over BLE from Linux and a Pixel 10a with GrapheneOS. 58 captures, one protocol, and the method that kept it honest."
date: 2026-10-05 09:00:00 +0200
tags: [embedded, bluetooth, reverse-engineering, flutter, linux, methodology]
description: >-
  COLMi R02, Realtek RTL8762E, BLE from Linux and a Pixel 10a. 16-byte
  framing, an unenforced checksum, and a clean-room method with evidence levels.
---

## The problem

The ring is a **COLMi R02**, a white-label smart ring in a family that ships
under at least a dozen brand names. The board is `RY02R_V3.0`, the firmware
`RY02R_3.01.00_250611`, and the radio chip is a **Realtek RTL8762E** (most
likely the RTL8762ESF variant, QFN24 package), which I confirmed over the air
with `Read Remote Version Information` before I ever opened the ring. It
measures heart rate, steps, SpO2, stress and HRV, stores the results on
itself, and hands them over Bluetooth Low Energy. The vendor's companion app
wants an account and a subscription; the ring does not.

The project, which I am calling **OpenHealth**, has a simple shape: read my
own ring's data over BLE with my own software, store it on my own phone, and
never talk to the vendor again. No account, no cloud, no tracking. The
hardware stays stock; the software is mine.

The setup:

- **Ring:** COLMi R02, one unit, no second one to sacrifice. The firmware
  cannot be restored if it is damaged, so every byte I sent to the ring had
  to be a byte I was allowed to send.
- **PC:** Linux, BlueZ 5.85, Python with [bleak](https://github.com/hbldh/bleak)
  and `uv`, a Realtek USB Bluetooth controller.
- **Phone:** Pixel 10a with GrapheneOS (Android 17), running the Flutter app
  **OwnRing**, which has no `INTERNET` permission.
- **Reference:** an Apple Watch for comparing heart-rate and step values.

The work took a week, and most of it was not reverse engineering. It was
setting up the Bluetooth stack, writing the capture tooling, and building the
method that made the protocol document trustworthy.

This article is about the protocol, the method, and the app. It is
deliberately limited to protocol facts and methodology. The captures contain
my health data, so they stay in a private repository, and the ring's
Bluetooth address is not in this article. What is here is everything you need
to do the same thing to your own device, and none of what you should not
publish.

## Working through it

### The first problem was not the ring

The first thing I built was a Python script that connects to the ring with
[bleak](https://github.com/hbldh/bleak) and reads its GATT structure. The
first thing that happened was that the connection dropped about 100
milliseconds after connecting, every time, with bleak reporting
`GATT Protocol Error: Unlikely Error`.

A `btmon` capture showed who was doing it. The sequence, right after the
connection was established:

1. the host sends `SMP: Pairing Request` (NoInputNoOutput, no bonding);
2. the ring answers `Pairing Failed: Invalid parameters (0x0a)`;
3. the host sends `Disconnect`, reason `Authentication Failure (0x05)`.

The local Bluetooth stack was starting a pairing that the ring refused, and
then giving up on the link. Two independent things made BlueZ pair. The ring
exposes a HID-over-GATT service, even though it is not a keyboard or a mouse,
and with `LEAutoSecurity=true` (the default) BlueZ's HID plugin treats every
such device as input hardware and upgrades its security at once. The fix is
one line in `/etc/bluetooth/input.conf`:

```sh
sudo sed -i 's/^#\?LEAutoSecurity=.*/LEAutoSecurity=false/' /etc/bluetooth/input.conf
sudo systemctl restart bluetooth
```

The second trigger is nastier: the ring has one GATT handle that answers a
read with `Insufficient Authentication`, and BlueZ's ATT layer reacts to that
error by starting a pairing itself. No setting turns that off. The only
remedy is to never read that handle. The tools therefore carry a
`--skip-handle 67` flag, and 67 is a number that appears in every command
example in the repository.

The second problem was `hcitool`. I used `hcitool leinfo` to read the peer's
link-layer version, which is the clean way to identify the radio chip without
touching the device's firmware. It timed out, and then every subsequent
connection attempt from BlueZ failed with `Command Disallowed (0x0c)`. The
reason: hcitool's legacy `LE Create Connection` request was still pending in
the controller, and while it is pending the controller refuses every new
create-connection command, including the kernel's extended one. The fix is to
cancel the stuck request:

```sh
sudo hcitool cmd 0x08 0x000e
```

which sends `LE Create Connection Cancel`. The status tells you whether
anything was stuck: `0x00` means a request was pending and is now cancelled,
`0x0c` means nothing was pending and the cause is elsewhere. I have not used
`hcitool leinfo` on that machine since.

Both problems are in the stack, not the ring, and both took longer to find
than any part of the protocol. I mention them because a week of reverse
engineering a €40 ring is roughly half a week of reverse engineering your own
Linux Bluetooth stack, and the fixes are not written down anywhere I could
find.

### What the ring is

With the connection stable, the inventory is short. Seven services, all
readable without pairing except the one handle. Two of them matter:

| Service | Write | Notify | Role |
|---|---|---|---|
| `6e40fff0-b5a3-…` | handle 15 | handle 17 | command channel |
| `de5bf728-d711-…` | handle 21 | handle 23 | big-data channel |

The first is a clone of the Nordic UART service — bleak even labels the
characteristics "Nordic UART RX/TX" — but with a different base UUID, so the
chip vendor's standard OTA service is absent. The second is where the
multi-payload measurements come back. Everything else is identity information,
a mystery service under `0xFEE7`, and the HID service that causes the pairing
problem above.

The chip was identified over the air, without opening the ring:
`Read Remote Version Information` reports manufacturer Realtek, subversion
`0x8762`. That
is the RTL8762E, a Bluetooth 5.2 LE SoC with a Cortex-M0+ at 40 MHz and 512
KB of in-package flash on the variant the ring uses. The ring reports
Bluetooth 5.0 at the link layer, which the datasheet's 5.2 does not
contradict: the firmware's stack sets what the link layer announces. The
practical consequence of the chip identification is not curiosity. It is that
the documented recovery path for this chip requires removing the epoxy and
flashing images I do not have, and the stock firmware cannot be backed up in
plain text because the flash read-back is encrypted. A mistake can cost the
ring its firmware for good.

### The command channel

The protocol is a 16-byte framing over the command channel, and the framing
is the whole story. Requests are exactly 16 bytes: byte 0 is the command,
bytes 1–14 are payload padded with zeros, byte 15 is a checksum. I verified
the "exactly" the hard way. A length scan sending zero packets of 1 to 20
bytes got a reply only for 16. Every other length was accepted at the ATT
level and silently ignored. An accidental 224-byte write of fourteen valid
packets back to back was also accepted without an ATT error, executed
nothing, and changed nothing. One packet per write, always.

The checksum is the sum of bytes 0–14 modulo 256. And the ring does not check
it. Packets with a deliberately wrong sum were answered the same way as
packets with a correct one. The ring sets the checksum correctly on its own
packets, every time, so the field is useful for validating replies and
meaningless for requests.

Replies are 16 bytes on the notify handle, repeating the command byte. The
first reply in the first capture arrived after 67 milliseconds. An unknown
command — byte 0 is `0x00` — is always answered with `80 ee 00 … 00 6e`,
whatever the rest of the packet contains. My working hypothesis is that
`0x80` is command `0x00` with the top bit set as an error flag. It fits every
observation so far.

Multi-packet replies use a header packet followed by numbered data packets.
"No data" is a single `<cmd> ff` packet. If the link drops in the middle of a
reply, the rest of the transfer arrives unasked shortly after the next
connection, so a client has to be able to ignore packets that do not belong
to the request it is currently waiting for.

The settings share one pattern: byte 1 is an action (1 = read, 2 = write),
byte 2 is on/off. There is one inconsistency worth knowing, because it is the
kind of thing that breaks a naive decoder: for SpO2, stress and HRV, `00` is
off and `01` is on. For heart rate, off is `02` and on is `01`. The ring does
not seem to have a single idea of what zero means.

The command table that matters for an app:

| Request | Reply | Meaning |
|---|---|---|
| `03` | `03 <percent> <charging> …` | battery |
| `01 YY MM DD hh mm ss` (BCD) | `2f f4 …`, then `01` + capability bitfield | set clock |
| `15 <midnight timestamp LE32>` | header + data, or `15 ff` | heart-rate history of one day |
| `16 01` / `16 02 <on> <min> <b4>` | setting read / write | heart-rate setting |
| `2c`, `36`, `38` + `01`/`02 <on>` | `<id> 01 <on>` | SpO2, stress, HRV setting |
| `37 <day>`, `39 <day>` | header + data, or `<id> ff` | stress, HRV history |
| `43 <day> …` | header + entries, or `43 ff` | steps of one day |
| `69 01 01` / `69 01 04` | `69 01 00 00`, then ten `69 01 00 <bpm>` | live heart rate start / stop |

The big-data channel uses a different frame: `bc <data id> <length LE16> <CRC LE16>`
plus payload, with the CRC being CRC-16/MODBUS over the payload only. The only
frame the firmware ever answers is the SpO2 request, and the reply is split
across several notifications that you reassemble until you have 6 + length bytes.

### The clock, and how to decide an encoding

Every stored value is filed under the ring's own clock, so the clock is not a
nice-to-have. The set-clock command takes six date and time fields, and the
public sources disagreed on whether they are plain binary or BCD. Rather than
trust either, I set the clock to the test value `10 09 10 10 10 10`, which is
valid in both encodings and means different things in each: **2016-09-16
16:16:16** as binary, **2010-09-10 10:10:10** as BCD. The prediction, written
down before the run: new heart-rate values will appear under exactly one of
the two dates. They appeared under 2010-09-10, at the slots ten, fifteen and
twenty minutes after the test value. BCD confirmed, binary refuted.

The pattern is worth keeping: when two encodings are both plausible, pick a
test value that is valid in both and lands somewhere different in each. The
device then answers the question for you, and the answer is a capture rather
than an argument.

The clock keeps local wall time with no time zone, so it has to be reset
after every daylight-saving change. Mine is set to local time, and the next
reset is on 2026-10-25.

### What the measurements mean

The ring stores a day as a block whose raster is fixed in the block's header.
Heart rate is one byte per 5-minute slot. Stress and HRV are in 30-minute
slots, with the day selected by byte 1 of the request (0 = today, 1 =
yesterday). SpO2 is hourly min/max over the big-data channel, current day
only. Steps are one entry per hour with movement, and the slot byte counts
quarter hours from midnight.

The step slots took three readings to pin down, and the first two were wrong:

| Reading | Status |
|---|---|
| 15-minute slots (one public source) | refuted: 100 steps walked at 00:41–00:47 would be slot 2 or 3, the ring reported 0 |
| hourly slots (my follow-up) | refuted: after the first worn night the slots went up to 40, more than 23 |
| quarter-hour slots, one entry per hour | confirmed: predicted a single new entry in slot 48 for a walk after 12:25, and that is what arrived |

The confirmation matters more than the answer. A walk with 1957 steps
measured by the phone came back from the ring within 1.6 percent. A hundred
deliberately counted steps came back 35 percent high, which is the kind of
result that tells you the counter is real and that your counted steps are
suspicious. Restless sleep counts as steps, which is the ring's problem, not
mine.

Two more findings from the same week. The heart-rate interval setting accepts
1 to 120 minutes and takes effect immediately, but the day's history keeps its
5-minute raster, so an interval that is not a multiple of 5 falls into the
slots irregularly. And the ring announces itself: it sends an unsolicited
`73 01` packet a moment after each automatic measurement, exactly at the
measurement pace, which means an app can fetch new values on that signal
instead of polling. It also sends `73 04` at hh:00:12 every hour, and a live
step counter `73 12` about every 0.8 seconds while you walk. The last one I
found during a midnight walk, and the final value before midnight was 0.6
percent below the sum of the stored hour entries. The gap is unexplained.

### Live heart rate

Besides the stored histories, the ring can measure on demand. The sequence:
send `69 01 01`, wait 25.5 seconds of warm-up, receive ten values about every
0.5 seconds, and the measurement ends by itself. Without skin contact the
ring answers the start with `69 01 01` after 3.5 seconds and delivers
nothing, which is exactly the signal an app needs to know the ring is not on
the finger. The stops are `6a 01 00 00` and `69 01 04`, and the app sends both
on every connection, because a stop that is lost is a battery that is drained.

The weak point is the connection, not the measurement. While worn, the link
drops whenever the hand or the body shields the ring's tiny antenna for more
than five seconds, because the ring itself requested a five-second
supervision timeout. Four connection attempts in a row failed on one
afternoon; the run only succeeded with the hand held still about thirty
centimetres from the PC. An app that samples on its own has to reconnect,
resume, and resend stops, and it has to accept that a dropped link ends the
series.

### The allowlist, because there is no recovery

With one ring and no restore path, the safety model is an allowlist of hex
prefixes. Every tool refuses to start without it, and a new entry needs two
things: public research saying the command is read-only, and a harmless
capture of it on this ring. Entries are added by issue and pull request,
never on the side.

The allowlist is the real protection, not the blocklist. The blocklist names
what public sources happen to describe — `0x08` reboot or power off, `0xFF`
restore, `0x10` an inconsistent "blink twice", `0x01` the clock, which one
source warns resets the ring with its data. But an undocumented byte might
still reach update or flash code, and how the firmware update is triggered is
not public. So the rule is inverted: only bytes proven harmless are sent, and
everything else is blocked by default.

There was one lesson that cost a near-miss. The guard only looks at the start
of a write. An unquoted shell variable once sent fourteen packets as one
224-byte write, and the guard let it through because it began with an
approved prefix. All fourteen packets happened to be approved, and the ring
ignored the long write anyway, but a blocked command could have hidden behind
an allowed prefix. Since then the guard rejects every write longer than 16
bytes, before connecting.

The single exception to "read only" is the live heart-rate start and its two
stops, approved for the app on the condition that every connection sends the
stops. Everything else stays read-only.

## The method

The protocol is the result. The method is what made the result trustworthy,
and it is the part I would reuse on any device.

**Clean room.** The protocol was learned only by watching the ring: dump the
GATT structure, capture notifications, send commands, read the answers. Two
things are forbidden: the source code of other reverse-engineering projects,
and the vendor's companion app. The first for a legal reason — the largest
of them is AGPL, and its code would pull the whole app under AGPL — and the
second on principle. Public prose, wikis, blog posts, protocol descriptions,
may be read. It serves exactly two purposes: as a safety bound (what to
block) and as a prediction for my own experiment. It never goes into the
protocol document directly. There, my own result is written, with a note that
the prediction came from research.

**Evidence levels.** Every statement in the protocol document has a status
and a capture reference:

| Status | Meaning |
|---|---|
| observed | a measured value or seen behaviour, no interpretation |
| hypothesis | an interpretation derived from captures, not yet tested on purpose |
| confirmed | a later experiment met a prediction derived from the interpretation |
| refuted | an experiment missed the prediction; the entry stays, so nobody repeats the mistake |

A prediction that can fail is what turns a hypothesis into "confirmed". The
step slots above are the clean example: three readings, two refuted, one
confirmed, all kept in the document with their captures.

**Captures are never edited.** A run is logged as self-describing JSONL —
one event per line, with the tool's commit, the library versions, the
arguments, and a note stating the purpose and the prediction. If the run
supports a statement, the log is copied unchanged to a numbered capture and
registered in an index. A correction is a new capture. Raw Bluetooth
monitoring files are never committed, because they record every keystroke of
every Bluetooth keyboard on the machine, including passwords. Only filtered
excerpts of the ring's own connection are stored, with the filter command in
the file header.

**Automatic checks.** The test suite fails if a capture breaks the naming
scheme, if a reference in the protocol document points to a missing file, if
a capture is not a valid log, or if an allowlist entry lacks a justification.
The decoders are tested against the real captures, which makes the captures
the drivers' test data. A pull request that only adds captures changes no
code, so its test cannot fail "before the fix" in the usual sense. The
project then shows the test can fail another way: run it against a capture
with the opposite behaviour, or mutate the decoder for a moment and restore
it. The failing output goes in the pull request body.

**Ask the person before showing the data.** Whether the ring lay on the
charger, or whether I walked during the night, was asked before I saw the
ring's answer, so the reply counts as an independent check. Ten live
measurements without a single value turned out to be a ring lying on the
desk. One control cycle after fixing the condition saved a wrong conclusion.

## The app

The app is **OwnRing**, a Flutter application that talks to the ring over
`universal_ble`. It has one dependency besides the BLE library, and the
release APK has no `INTERNET` permission, which is checked by a script that
builds the release and fails if the permission is in the manifest. The data
stays on the phone.

The architecture follows from the protocol. The ring stores every measurement
itself, so pull is the source of truth and the unsolicited packets are only
triggers for earlier pulls. On connection the app pulls everything for today
plus the fourteen previous days — the vendor says up to seven, and asking for
twice that shows in the log exactly when the oldest day vanishes. Days
without data answer `ff` at once, so the fourteen-day fetch is cheap. The
first sync on the phone sent 68 requests in 7.3 seconds, all answered.

The safety rules carry over from the tools. The app can only send what the
driver offers by name, a test checks every such packet against the
allowlist, and it reads or subscribes only the firmware string and the reply
channel, never everything, so it never touches the protected handle. The
driver's tests read the same captures as the protocol document, so a change
that breaks the protocol breaks the build.

The app connects only when tapped. While debugging, the phone and the PC take
turns on the ring, which accepts one connection at a time, and a phone that
reconnected by itself would lock the PC out. It is a debugging decision, but
it is also the honest behaviour of an app that is not going to sit in the
background holding a link to a five-second-timeout device.

The live heart-rate switch is off by default and usable only while connected.
It starts a measurement, receives ten values, and starts the next one a
second after the tenth, which gives a value about every 31 seconds. Without
skin contact it shows "no skin contact" and retries after ten seconds. A
watchdog restarts the measurement if ten values do not arrive within forty
seconds. Live values are shown and logged, not stored, because the ring does
not store them either.

## The solution

Where the week ended:

1. The protocol is mapped for everything the app needs: battery, the four
   measurement settings, stored heart rate, stress, HRV, SpO2 and steps,
   live heart rate, and the clock. 58 numbered captures back every statement
   in the protocol document.
2. The method is written down so it can be repeated on the next ring: clean
   room, evidence levels, numbered captures, automatic checks.
3. The app pulls, stores and shows every stored measurement for today and
   fourteen previous days, plus live heart rate behind a switch, on a phone
   with no internet permission.
4. The tools on the PC — an explorer, a browser-based live monitor, and a
   sync into SQLite that resumes after dropouts — send only allowlisted
   reads, and the guard refuses any write longer than one 16-byte packet.

The general shape of the problem is a proprietary device whose data is
yours, held behind software you do not control. The answer is not to build
the device. It is to observe the device, document what you observe with a
method that makes every claim checkable, and build the thin client that uses
the documented facts.

## Conclusion

The protocol is small: exactly 16 bytes or nothing, a checksum the ring does
not check, a heart-rate setting where off is `02` and on is `01`, and a step
counter that is 1.6 percent accurate on a walk and 35 percent optimistic on
a hundred counted steps. The interesting part is not the protocol. It is
that every fact in it has a status and a capture, that the two wrong answers
about the step slots are still in the document next to the right one, and
that a ring with no recovery path got a week of careful, allowlisted
experiments instead of a sweep that might have bricked it.

The method transfers to any device you own and do not fully control: decide
the purpose and the prediction before the run, log everything as data you can
re-evaluate, keep the recordings that support a claim unchanged, and let the
test suite check that every citation resolves. The captures stay private,
because they contain health data. The facts do not have to.
