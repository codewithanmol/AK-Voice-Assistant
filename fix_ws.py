path = 'main.py'
with open(path, encoding='utf-8') as f:
    content = f.read()

old = '''                    # Check for wake phrase (word-bounded, so "back"/"pack" don't count as "ak")
                    if self._wake_match(hl, wake_phrases):
                        self._asleep = False
                        # Extract any command said AFTER the wake phrase.
                        rest = self._command_after_wake(heard, wake_phrases)
                        if rest and not self._wake_match(rest.lower(), wake_phrases):
                            hud = self._hud_wake_command(rest)
                            if hud is not None:
                                reply, speak = hud
                                if reply and speak:
                                    try:
                                        _ws_broadcast("assistant", reply)
                                    except Exception:
                                        pass
                                    self._say(reply)
                                continue
                            text = rest
                            try:
                                if text:
                                    _ws_broadcast("user", text)
                            except Exception:
                                pass
                        if not text:
                            self._say("Radhe Radhe! I'm awake and ready.")
                            should, reason = should_offer_mood_shloka()
                            if should:
                                self._mood_offered = True
                                self._say("Would you like to listen to a shloka of Bhagavad Gita according to your mood?")
                            continue
                    # Process the extracted command immediately
                    # Fall through to normal processing
                    text = text
                else:
                    # Awake mode — listen for commands'''

new = '''                    # Check for wake phrase
                    if self._wake_match(hl, wake_phrases):
                        self._asleep = False
                        rest = self._command_after_wake(heard, wake_phrases)
                        if rest and not self._wake_match(rest.lower(), wake_phrases):
                            hud = self._hud_wake_command(rest)
                            if hud is not None:
                                reply, speak = hud
                                if reply and speak:
                                    try:
                                        _ws_broadcast("assistant", reply)
                                    except Exception:
                                        pass
                                    self._say(reply)
                                continue
                            text = rest
                            try:
                                if text:
                                    _ws_broadcast("user", text)
                            except Exception:
                                pass
                        if not text:
                            self._say("Radhe Radhe! I'm awake and ready.")
                            should, reason = should_offer_mood_shloka()
                            if should:
                                self._mood_offered = True
                                self._say("Would you like to listen to a shloka of Bhagavad Gita according to your mood?")
                            continue
                else:
                    # Awake mode — listen for commands'''

if old not in content:
    print('NOT FOUND')
else:
    content = content.replace(old, new)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print('FIXED')
