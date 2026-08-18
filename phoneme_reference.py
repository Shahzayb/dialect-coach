"""Static en-US pronunciation reference, keyed **by phoneme**, never by first language.

Why keyed by phoneme: the lookup this project needs is *expected → produced*, and the
produced sound comes from Azure's `NBestPhonemes` — from what the speaker actually did on
this recording. A table keyed by L1 would guess at substitutions from a stereotype;
this one reads them off the evidence.

Three consumers are designed for here, so the data is written once:

1. `fallback_coach` and `ai_coach` read `articulation` and `minimal_pairs`.
2. A perception trainer reads `Phoneme.contrasts` on its own — a contrast already carries
   the pairs to play against each other.
3. A later accent feature adds `formants` to `Phoneme` and a bridging drill to `Contrast`,
   as new fields on the same entries rather than a second table.

**The symbols are Azure's, not a textbook's.** Verified against both committed fixtures:
Azure's en-US IPA is rhotic and carries no length marks — `ɝ ɚ ɹ ɔɹ ɪɹ oʊ eɪ`, never
`iː ɑː ɜː`. `normalise` maps the textbook spellings onto Azure's so a symbol typed by hand
still resolves, but the keys below are what actually arrives in a payload.

A missing entry degrades to "no articulation note for this pair" (`NO_NOTE`) and to an
empty list of pairs. It must never degrade to a *wrong* note: a confident, incorrect
instruction about where to put your tongue is worse than an admission that this pair has
not been written up yet.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Mapping

logger = logging.getLogger(__name__)

# What to say when a pair has no entry. Deliberately not advice.
NO_NOTE = (
    "No articulation note for this pair yet — the substitution itself is still what to "
    "drill."
)

# Cluster simplification is not a substitution and so has no (expected → produced) pair:
# the sound is weakened or dropped, and Azure reports it as a low score with no better
# alternate. `fallback_coach` uses this when the weak sound is at the end of a word that
# ends in two or more consonants.
FINAL_CLUSTER_NOTE = (
    "This is a final consonant cluster. Cutting the last sound short is what makes "
    "\"asked\" land as \"ask\" and \"missed\" as \"miss\" — hold the final consonant and "
    "release it, even when the next word starts with one too."
)

# Textbook and keyboard spellings mapped onto the symbols Azure actually emits. Stress
# marks and length marks are stripped rather than mapped.
_STRIP = "ˈˌː.‿ "

_ALIASES: dict[str, str] = {
    "ɡ": "g",      # U+0261 script g, what many IPA fonts and sources use
    "r": "ɹ",      # the trill spelling of the American approximant
    "ɜ": "ɝ",      # ɜː once the length mark is stripped
    "ɛɚ": "ɛɹ",
    "ɒ": "ɑ",      # British LOT
    "e": "ɛ",      # some sources write DRESS as /e/
    "əʊ": "oʊ",    # British GOAT
    "ʧ": "tʃ",
    "ʤ": "dʒ",
    "ɫ": "l",      # dark l is an allophone, not a separate target
    "ʁ": "ɹ",
    "ɾ": "t",      # the flap is an allophone of /t/
    "ɐ": "ʌ",
    "ɪə": "ɪɹ",
    "ɔə": "ɔɹ",
    "ʊə": "ʊɹ",
    "ɑː": "ɑ",
    "aːɹ": "ɑɹ",
}


def normalise(symbol: str | None) -> str:
    """Reduce a symbol to the spelling Azure uses. Empty string when there is nothing to read.

    Case is left alone: IPA is case-significant (`ɪ` and `I` are different claims), so
    lowercasing would silently merge symbols rather than tidy them.
    """
    if not symbol:
        return ""
    cleaned = "".join(character for character in symbol if character not in _STRIP)
    return _ALIASES.get(cleaned, cleaned)


@dataclass(frozen=True)
class Contrast:
    """One expected → produced substitution: what it costs, and what to drill against it.

    `minimal_pairs` are real word pairs differing only in this sound. They are the whole
    point of the entry: "your /θ/ is weak" is a grade, "think became sink" is a drill.
    An empty tuple is honest — some substitutions have no minimal pair in English, and
    inventing one would teach a word that does not exist.
    """

    produced: str
    why_it_matters: str
    minimal_pairs: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Phoneme:
    """One target sound, with the substitutions that have been written up for it."""

    symbol: str
    label: str
    kind: str                       # consonant | vowel | diphthong | r-coloured
    articulation: str               # tongue / lip / airflow, concrete enough to act on
    examples: tuple[str, ...] = ()
    contrasts: Mapping[str, Contrast] = field(default_factory=dict)


def _c(produced: str, why: str, *pairs: tuple[str, str]) -> Contrast:
    return Contrast(produced=produced, why_it_matters=why, minimal_pairs=pairs)


def _p(symbol: str, label: str, kind: str, articulation: str,
       examples: tuple[str, ...] = (), *contrasts: Contrast) -> Phoneme:
    return Phoneme(
        symbol=symbol,
        label=label,
        kind=kind,
        articulation=articulation,
        examples=examples,
        contrasts={contrast.produced: contrast for contrast in contrasts},
    )


# The pairs read (word with the *expected* sound, word with the *produced* sound), always
# in that order, so a drill can be generated without inspecting which is which.
_SPELLING_VOWEL = (
    "An unstressed syllable given its spelling vowel puts a beat where English does not "
    "have one. The listener hears the rhythm as foreign before they hear any single sound."
)

_CONSONANTS: tuple[Phoneme, ...] = (
    # --- The seed order: the sounds this project was built to catch -------------------
    _p("θ", "voiceless th", "consonant",
       "Tongue tip lightly between the teeth, or just touching the edge of the top ones, "
       "and blow a steady stream of air past it with no voicing. The tongue has to be "
       "visible — if it stays behind the teeth you get /t/ or /s/ instead.",
       ("think", "three", "bath", "month"),
       _c("s", "The word becomes another word: think lands as sink, mouth as mouse. The "
               "most intelligibility-costly substitution in this set, because both sides "
               "are everyday words.",
          ("think", "sink"), ("thick", "sick"), ("thing", "sing"), ("path", "pass"),
          ("mouth", "mouse")),
       _c("t", "Thin becomes tin, three becomes tree. The sentence still parses, so nobody "
               "asks you to repeat it and the error never gets corrected.",
          ("thin", "tin"), ("thick", "tick"), ("three", "tree"), ("thought", "taught"),
          ("path", "pat")),
       _c("f", "Three becomes free and thirst becomes first. It is also heard as a speech "
               "difficulty rather than as an accent, which is its own cost.",
          ("thin", "fin"), ("three", "free"), ("thought", "fought"), ("thirst", "first"))),

    _p("ð", "voiced th", "consonant",
       "The same tongue position as /θ/ — tip at the top teeth — with the voice on. A hand "
       "on your throat should buzz for the whole sound. It is short: in the, this and that "
       "it is barely more than a buzz before the vowel.",
       ("this", "that", "brother", "breathe"),
       _c("d", "They becomes day, though becomes dough. These are the highest-frequency "
               "words in English, so this one substitution colours nearly every sentence.",
          ("they", "day"), ("then", "den"), ("though", "dough"), ("breathe", "breed"),
          ("other", "udder")),
       _c("z", "Breathe lands as breeze, lathe as laze — the tongue pulls back behind the "
               "teeth instead of touching them.",
          ("breathe", "breeze"), ("teethe", "tease"), ("lathe", "laze")),
       _c("v", "That becomes vat, clothe becomes clove. Rarer than the /d/ swap, but it "
               "costs a real word every time.",
          ("that", "vat"), ("clothe", "clove"))),

    _p("v", "v", "consonant",
       "Top teeth resting on the inside of the bottom lip, voice on, air forced through the "
       "gap. The lips must never meet each other — the moment they do you get /b/ or /w/.",
       ("very", "vine", "seven", "love"),
       _c("w", "Vest and west, vine and wine are different words, and both are common "
               "enough that the listener guesses from context and sometimes guesses wrong.",
          ("vest", "west"), ("vine", "wine"), ("veil", "whale"), ("verse", "worse"),
          ("vet", "wet")),
       _c("b", "Curve becomes curb, vote becomes boat. The lips meeting is the entire "
               "difference — /v/ never closes them.",
          ("vote", "boat"), ("van", "ban"), ("curve", "curb"), ("marvel", "marble"),
          ("vest", "best")),
       _c("f", "Losing the voicing turns leave into leaf and prove into proof, usually at "
               "the end of a word where it shortens the vowel as well.",
          ("leave", "leaf"), ("prove", "proof"), ("save", "safe"), ("van", "fan"))),

    _p("w", "w", "consonant",
       "Round the lips into a tight circle and pull the back of the tongue up toward the "
       "soft palate, then move straight into the vowel. Nothing touches anything: no teeth "
       "on the lip, no tongue on the ridge.",
       ("west", "wine", "away", "quick"),
       _c("v", "West lands as vest. The fix is at the lips: /w/ rounds them, /v/ needs the "
               "top teeth on the bottom lip.",
          ("west", "vest"), ("wine", "vine"), ("whale", "veil"), ("worse", "verse"),
          ("wet", "vet"))),

    # --- Remaining consonants ---------------------------------------------------------
    _p("p", "p", "consonant",
       "Both lips together, then released with a real puff of air at the start of a "
       "stressed syllable — a hand in front of your mouth should feel it.",
       ("pen", "open", "cup", "spot"),
       _c("f", "The lips stop meeting: pine becomes fine, copy becomes coffee.",
          ("pine", "fine"), ("pan", "fan"), ("copy", "coffee"), ("leap", "leaf")),
       _c("b", "Voicing it turns cap into cab and rope into robe.",
          ("pat", "bat"), ("pin", "bin"), ("cap", "cab"), ("rope", "robe"))),

    _p("b", "b", "consonant",
       "Both lips together with the voice already on, then released. Shorter and softer "
       "than /p/, and with no puff of air after it.",
       ("book", "big", "about", "cab"),
       _c("p", "Losing the voice turns cab into cap and robe into rope.",
          ("bat", "pat"), ("bin", "pin"), ("cab", "cap"), ("robe", "rope")),
       _c("v", "The lips open into a gap instead of meeting: boat becomes vote, curb "
               "becomes curve.",
          ("boat", "vote"), ("ban", "van"), ("curb", "curve"), ("marble", "marvel"))),

    _p("t", "t", "consonant",
       "Tongue tip on the bony ridge just behind the top teeth — not on the teeth "
       "themselves — then released with a puff of air at the start of a stressed syllable. "
       "A dental /t/ costs no words but is the most audible accent marker in the set.",
       ("time", "take", "water", "light"),
       _c("d", "Voicing it turns write into ride and bet into bed.",
          ("write", "ride"), ("bet", "bed"), ("latter", "ladder"), ("town", "down"),
          ("time", "dime")),
       _c("tʃ", "The release drags backward into a /ʃ/, so tip lands as chip and tin as "
                "chin.",
          ("tip", "chip"), ("tin", "chin"), ("taste", "chased"))),

    _p("d", "d", "consonant",
       "Tongue tip on the ridge behind the top teeth, voice on through the closure, then "
       "released cleanly. As with /t/, the tip stays off the teeth themselves.",
       ("day", "did", "ladder", "made"),
       _c("t", "Dropping the voicing turns ride into write and bed into bet — most often at "
               "the end of a word, where it also takes the past tense with it.",
          ("ride", "write"), ("bed", "bet"), ("ladder", "latter"), ("made", "mate")),
       _c("ð", "The tongue slips forward onto the teeth: den becomes then, day becomes "
               "they.",
          ("den", "then"), ("day", "they"), ("dough", "though"))),

    _p("k", "k", "consonant",
       "Back of the tongue up against the soft palate, released with a puff of air. "
       "Nothing at the front of the mouth moves.",
       ("cat", "key", "back", "school"),
       _c("g", "Voicing turns coat into goat and back into bag.",
          ("coat", "goat"), ("back", "bag"), ("cap", "gap"), ("ankle", "angle"))),

    _p("g", "hard g", "consonant",
       "Back of the tongue against the soft palate with the voice on, released cleanly and "
       "without the puff of air that follows /k/.",
       ("go", "bag", "again", "ghost"),
       _c("k", "Losing the voice turns goat into coat and bag into back.",
          ("goat", "coat"), ("bag", "back"), ("gap", "cap"))),

    _p("tʃ", "ch", "consonant",
       "A stop and a fricative in one movement: tongue tip on the ridge, then released into "
       "a /ʃ/ with the lips slightly rounded and no voicing.",
       ("chip", "teacher", "watch", "much"),
       _c("ʃ", "Dropping the stop at the front turns chip into ship and chair into share.",
          ("chip", "ship"), ("chair", "share"), ("cheap", "sheep"), ("watch", "wash")),
       _c("dʒ", "Adding voice turns batch into badge and cheap into jeep.",
          ("batch", "badge"), ("cheap", "jeep"), ("rich", "ridge"))),

    _p("dʒ", "j as in judge", "consonant",
       "A stop and a fricative in one: tongue tip on the ridge, then released into a /ʒ/ "
       "with the lips slightly rounded and the voice on throughout.",
       ("judge", "jam", "age", "bridge"),
       _c("z", "The stop at the front goes missing, so rage lands as raze and budge as "
               "buzz.",
          ("rage", "raze"), ("budge", "buzz"), ("jest", "zest")),
       _c("tʃ", "Losing the voicing turns badge into batch and jeep into cheap.",
          ("badge", "batch"), ("jeep", "cheap"), ("ridge", "rich"))),

    _p("f", "f", "consonant",
       "Top teeth on the inside of the bottom lip, air pushed through the gap, no voicing. "
       "The lips never meet.",
       ("fine", "off", "laugh", "before"),
       _c("p", "The lips close and fine becomes pine, coffee becomes copy.",
          ("fine", "pine"), ("fan", "pan"), ("coffee", "copy"), ("leaf", "leap"),
          ("four", "pour")),
       _c("v", "Adding voice turns leaf into leave and safe into save.",
          ("leaf", "leave"), ("safe", "save"), ("proof", "prove"), ("fan", "van"))),

    _p("s", "s", "consonant",
       "Tongue tip close behind the top teeth with a narrow groove down the middle, lips "
       "spread, air hissing high and thin. No voicing.",
       ("see", "stop", "pass", "miss"),
       _c("ʃ", "Sip becomes ship, sell becomes shell — the tongue slides back and the lips "
               "round.",
          ("sip", "ship"), ("sell", "shell"), ("mass", "mash"), ("seat", "sheet")),
       _c("z", "Voicing it turns bus into buzz and price into prize.",
          ("bus", "buzz"), ("price", "prize"), ("ice", "eyes")),
       _c("θ", "The tongue tip comes forward past the teeth and sink lands as think.",
          ("sink", "think"), ("sing", "thing"), ("pass", "path"), ("mouse", "mouth"))),

    _p("z", "z", "consonant",
       "Tongue tip near — not touching — the ridge behind the top teeth, voice on, air "
       "hissing continuously through the narrow gap. It has to keep buzzing to the end of "
       "the word, which is where it is usually lost.",
       ("zoo", "buzz", "easy", "prize"),
       _c("s", "Losing the voicing turns prize into price and eyes into ice, and it takes "
               "the plural and third-person endings with it.",
          ("prize", "price"), ("eyes", "ice"), ("buzz", "bus"), ("peas", "peace"),
          ("raise", "race")),
       _c("dʒ", "Stopping the air before releasing it turns raze into rage and buzz into "
                "budge.",
          ("raze", "rage"), ("buzz", "budge"), ("zest", "jest"))),

    _p("ʃ", "sh", "consonant",
       "Tongue blade raised toward the back of the ridge with a wide channel, lips pushed "
       "forward and slightly rounded, air hissing at a lower pitch than /s/.",
       ("she", "shop", "wash", "nation"),
       _c("s", "Ship becomes sip, mash becomes mass: the tongue is too far forward and the "
               "lips are not rounded.",
          ("ship", "sip"), ("shell", "sell"), ("mash", "mass"), ("sheet", "seat")),
       _c("tʃ", "A stop appears at the front and ship lands as chip, share as chair.",
          ("ship", "chip"), ("share", "chair"), ("sheep", "cheap"), ("wash", "watch"))),

    _p("ʒ", "zh as in measure", "consonant",
       "The voiced partner of /ʃ/: tongue blade toward the back of the ridge, lips forward, "
       "voice on. It never starts an English word, which is why it is easy to miss.",
       ("measure", "vision", "beige", "usual"),
       _c("ʃ", "Losing the voice makes confusion sound like Confucian.",
          ("confusion", "Confucian")),
       _c("dʒ", "It gains a stop at the front, so a sound English keeps smooth inside a "
                "word lands as the j of judge."),
       _c("z", "The tongue moves forward and beige lands as bays.",
          ("beige", "bays"))),

    _p("h", "h", "consonant",
       "Just breath: the mouth is already in position for the vowel that follows and air "
       "passes through an open throat. Nothing touches, and nothing vibrates.",
       ("hat", "behind", "who", "ahead")),

    _p("m", "m", "consonant",
       "Lips together, voice on, air out through the nose. The only lip-closing sound that "
       "keeps flowing rather than being released.",
       ("man", "some", "summer", "climb")),

    _p("n", "n", "consonant",
       "Tongue tip on the ridge behind the top teeth, voice on, air out through the nose "
       "around the sides of the closure.",
       ("no", "run", "dinner", "again"),
       _c("ŋ", "The closure moves to the back of the mouth: thin becomes thing, sin becomes "
               "sing.",
          ("thin", "thing"), ("sin", "sing"), ("ban", "bang"), ("ran", "rang")),
       _c("l", "The air stops going through the nose: night becomes light, no becomes low.",
          ("night", "light"), ("not", "lot"), ("no", "low"))),

    _p("ŋ", "ng", "consonant",
       "Back of the tongue against the soft palate, voice on, air out through the nose. "
       "There is no /g/ released after it in singer or running — only in finger and anger.",
       ("sing", "long", "think", "finger"),
       _c("n", "The closure moves forward to the ridge and sing lands as sin.",
          ("sing", "sin"), ("thing", "thin"), ("rang", "ran"))),

    _p("l", "l", "consonant",
       "Tongue tip on the ridge behind the top teeth with the sides down, so the air "
       "escapes around them. At the end of a word or before a consonant the back of the "
       "tongue also humps up — the dark l of feel and milk — and skipping that is what "
       "makes it sound like a /w/ or vanish altogether.",
       ("light", "feel", "milk", "allow"),
       _c("w", "A dark /l/ collapsing into a /w/ makes light sound like white and feel "
               "like few.",
          ("light", "white"), ("led", "wed"), ("lake", "wake"), ("lie", "why")),
       _c("ɹ", "The tip leaves the ridge and curls back: light becomes right, collect "
               "becomes correct.",
          ("light", "right"), ("lock", "rock"), ("glass", "grass"), ("play", "pray"),
          ("collect", "correct")),
       _c("n", "The air goes through the nose instead of around the tongue and light lands "
               "as night.",
          ("light", "night"), ("lot", "not"), ("low", "no"))),

    _p("ɹ", "American r", "consonant",
       "Bunch the middle of the tongue up and pull the tip back so it points at the roof of "
       "the mouth without touching it, with the lips slightly rounded. The tip must not tap "
       "the ridge: a tapped or trilled r is the most audible substitution in this set.",
       ("red", "around", "car", "very"),
       _c("l", "Right becomes light, correct becomes collect.",
          ("right", "light"), ("rock", "lock"), ("grass", "glass"), ("pray", "play"),
          ("correct", "collect")),
       _c("w", "The tongue stays flat and only the lips do the work, so red lands as wed.",
          ("red", "wed"), ("right", "white"), ("rake", "wake"))),

    _p("j", "y as in yes", "consonant",
       "Tongue high and forward as if for /i/, then moving straight into the next vowel "
       "without stopping anywhere.",
       ("yes", "you", "music", "onion"),
       _c("dʒ", "The tongue touches the ridge and stops the air, so year lands as jeer.",
          ("year", "jeer"), ("yam", "jam"), ("yet", "jet"))),
)


_VOWELS: tuple[Phoneme, ...] = (
    # --- Monophthongs, seed order first ------------------------------------------------
    _p("æ", "short a (TRAP)", "vowel",
       "Jaw dropped low, tongue forward and flat, lips spread wide. It is long and loud in "
       "American English — much closer to a flat, held 'aa' than to the short vowel most "
       "other languages put in this slot.",
       ("cat", "bad", "man", "have"),
       _c("ɛ", "Bad and bed, man and men. The vowel is carrying the meaning on its own, and "
               "these pairs turn up constantly in ordinary speech.",
          ("bad", "bed"), ("man", "men"), ("sad", "said"), ("bat", "bet"),
          ("had", "head")),
       _c("ʌ", "Cat becomes cut and ran becomes run — the tense of the sentence changes "
               "with the vowel.",
          ("cat", "cut"), ("bat", "but"), ("ran", "run"), ("ham", "hum"),
          ("match", "much")),
       _c("ɑ", "Cap becomes cop. Both are open vowels; the difference is that /æ/ is "
               "forward with spread lips and /ɑ/ is back with an open throat.",
          ("cap", "cop"), ("sack", "sock"), ("hat", "hot"), ("add", "odd"))),

    _p("ɛ", "short e (DRESS)", "vowel",
       "Tongue mid-high and forward, jaw about halfway open, lips relaxed and slightly "
       "spread. More open than /ɪ/, tighter and shorter than /æ/, and it never glides.",
       ("bed", "said", "many", "head"),
       _c("ɪ", "Pen and pin, desk and disk. Before /n/ these are already merged for some "
               "American speakers, which makes the remaining pairs matter more, not less.",
          ("bed", "bid"), ("pen", "pin"), ("desk", "disk"), ("set", "sit"),
          ("ten", "tin")),
       _c("eɪ", "The vowel stretches into a glide and wet becomes wait, sell becomes sail.",
          ("wet", "wait"), ("sell", "sail"), ("pen", "pain"), ("test", "taste"),
          ("bet", "bait")),
       _c("æ", "Bed lands as bad, men as man — the jaw drops too far.",
          ("bed", "bad"), ("men", "man"), ("said", "sad"), ("bet", "bat"))),

    _p("ɪ", "short i (KIT)", "vowel",
       "Tongue high and forward but relaxed, jaw barely moving, and short. Stopping short "
       "of a full /i/ is the whole point — the tongue never reaches the roof and the lips "
       "never spread into a smile.",
       ("sit", "ship", "bit", "live"),
       _c("i", "Ship becomes sheep, live becomes leave. English separates these by tongue "
               "tension and length rather than by spelling, so the page gives no clue.",
          ("ship", "sheep"), ("bit", "beat"), ("live", "leave"), ("fill", "feel"),
          ("sit", "seat")),
       _c("ɛ", "Bid lands as bed and sit as set: the jaw opens too far.",
          ("bid", "bed"), ("sit", "set"), ("disk", "desk"), ("pin", "pen"))),

    _p("i", "long ee (FLEECE)", "vowel",
       "Tongue high and pushed forward with real tension in it, lips spread as if smiling, "
       "and held noticeably longer than /ɪ/.",
       ("see", "sheep", "leave", "machine"),
       _c("ɪ", "Sheep becomes ship — the tongue relaxes and the vowel is cut short.",
          ("sheep", "ship"), ("beat", "bit"), ("leave", "live"), ("feel", "fill"),
          ("seat", "sit"))),

    _p("ɑ", "broad a (LOT, PALM)", "vowel",
       "Jaw open wide, tongue low and pulled back, throat open, lips doing nothing. The "
       "most open vowel in the set — it is the sound a doctor asks you to make.",
       ("hot", "father", "stop", "calm"),
       _c("æ", "Cop becomes cap, hot becomes hat: the tongue comes forward and the lips "
               "spread.",
          ("cop", "cap"), ("sock", "sack"), ("hot", "hat"), ("odd", "add")),
       _c("ɔ", "Worth knowing before drilling it: most American speakers merge these, so "
               "cot and caught are genuinely the same word for them. Low priority unless "
               "the rest of the vowel is off too.",
          ("cot", "caught"), ("stock", "stalk"), ("don", "dawn"))),

    _p("ʌ", "short u (STRUT)", "vowel",
       "Tongue central and low-mid, jaw slightly open, lips completely relaxed. Nothing "
       "rounds and nothing spreads: it is the most neutral stressed vowel English has.",
       ("cut", "love", "done", "enough"),
       _c("ɑ", "Cut becomes cot — the tongue drops too far back and the throat opens.",
          ("cut", "cot"), ("hut", "hot"), ("cup", "cop"), ("duck", "dock")),
       _c("ʊ", "Luck becomes look and buck becomes book: the lips round when they should "
               "stay slack.",
          ("luck", "look"), ("buck", "book"), ("tuck", "took")),
       _c("æ", "Cut lands as cat, run as ran.",
          ("cut", "cat"), ("hum", "ham"), ("run", "ran"), ("but", "bat"))),

    _p("ɝ", "stressed r-vowel (NURSE)", "vowel",
       "The vowel *is* the r: bunch the middle of the tongue up and pull the tip back "
       "without touching anything, and hold that for the whole vowel. There is no separate "
       "vowel followed by an r — the r-colour runs through it from start to finish.",
       ("bird", "hurt", "learn", "word"),
       _c("æ", "The r-colour is dropped and the vowel opens out, so hurt lands as hat and "
               "turn as tan. Azure reports this against the vowel, not as a missing /ɹ/.",
          ("hurt", "hat"), ("turn", "tan"), ("burn", "ban")),
       _c("ɔɹ", "Bird becomes board, turn becomes torn: the tongue sits too far back and "
                "the lips round.",
          ("bird", "board"), ("turn", "torn"), ("were", "wore")),
       _c("ɑɹ", "Hurt becomes heart and burn becomes barn — the jaw opens under the "
                "r-colour.",
          ("hurt", "heart"), ("burn", "barn"), ("curb", "carb"))),

    _p("ə", "schwa", "vowel",
       "The rest position of the mouth: tongue central, jaw slack, lips neutral — and above "
       "all short. It only ever appears in unstressed syllables, and getting it short "
       "enough is most of what makes English rhythm sound like English.",
       ("about", "sofa", "support", "the"),
       _c("ʌ", "The same tongue position doing a different job: /ʌ/ takes stress and /ə/ "
               "never does. Hearing one for the other is a rhythm problem rather than a "
               "wrong word."),
       _c("ɛ", _SPELLING_VOWEL),
       _c("æ", _SPELLING_VOWEL),
       _c("ɑ", _SPELLING_VOWEL),
       _c("ɪ", _SPELLING_VOWEL),
       _c("oʊ", _SPELLING_VOWEL)),

    _p("ʊ", "short oo (FOOT)", "vowel",
       "Tongue high and back but relaxed, lips loosely rounded, and short. Less rounding "
       "and far less tension than /u/.",
       ("book", "put", "could", "full"),
       _c("u", "Full becomes fool, look becomes Luke — the lips tighten and the vowel is "
               "held too long.",
          ("full", "fool"), ("pull", "pool"), ("could", "cooed")),
       _c("ʌ", "Look becomes luck: the lips lose their rounding altogether.",
          ("look", "luck"), ("book", "buck"), ("took", "tuck"))),

    _p("u", "long oo (GOOSE)", "vowel",
       "Tongue high and back, lips pushed forward into a tight circle, held long. In "
       "American English it often starts slightly forward, so it is not a pure back vowel.",
       ("food", "blue", "school", "two"),
       _c("ʊ", "Fool becomes full and pool becomes pull — the vowel is cut short and the "
               "lips slacken.",
          ("fool", "full"), ("pool", "pull"), ("Luke", "look"))),

    _p("ɔ", "aw (THOUGHT)", "vowel",
       "Tongue low and back, lips rounded, jaw open. Many American speakers merge this with "
       "/ɑ/ so that caught and cot sound identical; that merge is standard American, not an "
       "error to correct.",
       ("thought", "law", "caught", "dog"),
       _c("ɑ", "Caught becomes cot. Standard for most American speakers — only worth "
               "drilling if you are deliberately keeping the distinction.",
          ("caught", "cot"), ("stalk", "stock"), ("dawn", "don"))),

    _p("ɚ", "unstressed r-vowel (letter)", "vowel",
       "The same r-colour as /ɝ/ but unstressed and short — the ending of water, letter and "
       "brother. It is a full r-coloured vowel in its own right, not a schwa with an r "
       "added afterwards.",
       ("water", "letter", "brother", "never"),
       _c("ə", "The r-colour is dropped and the ending goes non-rhotic in an accent that "
               "is rhotic everywhere else, which is why it stands out so sharply."),
       _c("ɑ", "The ending is given a full open vowel, so it takes a beat of its own and "
               "the word gains a stress English does not put there.")),

    # --- Diphthongs ---------------------------------------------------------------------
    _p("eɪ", "long a (FACE)", "diphthong",
       "Start at /ɛ/ and glide up toward /ɪ/ in one continuous movement, closing the jaw as "
       "you go. It has to move — holding it still collapses it into /ɛ/.",
       ("say", "wait", "make", "eight"),
       _c("ɛ", "The glide is dropped and wait becomes wet, sail becomes sell.",
          ("wait", "wet"), ("sail", "sell"), ("pain", "pen"), ("taste", "test"))),

    _p("aɪ", "long i (PRICE)", "diphthong",
       "Start with the jaw open around /ɑ/ and glide up to /ɪ/, closing as you go. The "
       "first half carries almost all of the length.",
       ("time", "my", "light", "buy"),
       _c("ɑ", "The glide never happens and light lands as lot.",
          ("light", "lot"), ("bite", "bot"))),

    _p("oʊ", "long o (GOAT)", "diphthong",
       "Start with the lips loosely rounded and glide toward /ʊ/, tightening the rounding "
       "as you close. British and Indian Englishes start this vowel further forward, which "
       "is what makes it the most recognisable single vowel in the accent.",
       ("go", "boat", "know", "slow"),
       _c("ɔ", "The glide is flattened out and boat becomes bought, low becomes law.",
          ("boat", "bought"), ("coat", "caught"), ("low", "law"))),

    _p("aʊ", "ow (MOUTH)", "diphthong",
       "Start with the jaw wide open and glide back and up toward /ʊ/, rounding the lips as "
       "you close.",
       ("now", "house", "out", "down"),
       _c("oʊ", "The opening half is lost, so house lands as hose and loud as load.",
          ("house", "hose"), ("loud", "load"), ("now", "no"))),

    _p("ɔɪ", "oy (CHOICE)", "diphthong",
       "Start with rounded lips at /ɔ/ and glide up and forward to /ɪ/, unrounding as you "
       "go.",
       ("boy", "noise", "point", "enjoy"),
       _c("ɔ", "The glide is dropped and boil becomes ball, coil becomes call.",
          ("boil", "ball"), ("coil", "call"))),

    # --- R-coloured vowels. Azure emits these as single phonemes, not vowel + /ɹ/. ------
    _p("ɑɹ", "ar (START)", "r-coloured",
       "Jaw open for /ɑ/, then bunch the tongue for the r without ever tapping the ridge. "
       "One sound, not a vowel followed by a separate r.",
       ("car", "hard", "far", "start"),
       _c("ɑ", "The r-colour is dropped and card lands as cod, heart as hot.",
          ("card", "cod"), ("heart", "hot"), ("park", "pock")),
       _c("ɝ", "The jaw closes too far under the r-colour: barn becomes burn, heart becomes "
               "hurt.",
          ("barn", "burn"), ("heart", "hurt"), ("carb", "curb"))),

    _p("ɔɹ", "or (NORTH, FORCE)", "r-coloured",
       "Lips rounded as for /ɔ/ with the r-colour running through the whole vowel rather "
       "than arriving at the end of it.",
       ("more", "born", "door", "before"),
       _c("ɝ", "Born becomes burn, torn becomes turn — the lips lose their rounding.",
          ("born", "burn"), ("torn", "turn"), ("wore", "were")),
       _c("ɑɹ", "The jaw opens too far and born lands as barn, cord as card.",
          ("born", "barn"), ("cord", "card"), ("port", "part"))),

    _p("ɛɹ", "air (SQUARE)", "r-coloured",
       "Jaw at /ɛ/, then into the r with no separate vowel between the two.",
       ("air", "care", "there", "where"),
       _c("ɪɹ", "The jaw closes too far and hair lands as hear, chair as cheer.",
          ("hair", "hear"), ("chair", "cheer"), ("bear", "beer")),
       _c("ɝ", "Hair becomes her, care becomes cur.",
          ("hair", "her"), ("care", "cur"))),

    _p("ɪɹ", "ear (NEAR)", "r-coloured",
       "Start high and forward at /ɪ/ and move into the r without letting the jaw open.",
       ("here", "beer", "clear", "year"),
       _c("ɛɹ", "The jaw opens too far and beer lands as bear, cheer as chair.",
          ("beer", "bear"), ("hear", "hair"), ("cheer", "chair"))),

    _p("ʊɹ", "ure (CURE)", "r-coloured",
       "Lips rounded at /ʊ/, then into the r. Many American speakers use /ɔɹ/ here anyway, "
       "so sure and shore genuinely match for them.",
       ("sure", "tour", "pure", "cure"),
       _c("ɔɹ", "Tour becomes tore. Standard for a large share of American speakers, so "
                "this is low priority next to anything else flagged.",
          ("tour", "tore"), ("poor", "pour"))),
)

_ENTRIES: tuple[Phoneme, ...] = _CONSONANTS + _VOWELS


def _build(entries: tuple[Phoneme, ...]) -> dict[str, Phoneme]:
    """Index the entries by symbol, refusing to silently drop a duplicate.

    A repeated symbol would overwrite the earlier entry and take its contrasts with it —
    invisible in a table this size, and exactly the kind of failure this project keeps
    finding the expensive way.
    """
    registry: dict[str, Phoneme] = {}
    for entry in entries:
        if entry.symbol in registry:
            raise RuntimeError(f"Duplicate phoneme entry for /{entry.symbol}/.")
        registry[entry.symbol] = entry
    return registry


PHONEMES: Mapping[str, Phoneme] = _build(_ENTRIES)


def symbols() -> frozenset[str]:
    """Every symbol the table covers. Used by tests to check fixture coverage."""
    return frozenset(PHONEMES)


def lookup(expected: str | None) -> Phoneme | None:
    """The entry for a target sound, or None when it is not in the table."""
    return PHONEMES.get(normalise(expected))


def contrast(expected: str | None, produced: str | None) -> Contrast | None:
    """The entry for one expected → produced substitution, or None when unwritten."""
    entry = lookup(expected)
    if entry is None:
        return None
    return entry.contrasts.get(normalise(produced))


def articulation_for(expected: str | None, produced: str | None = None) -> str:
    """How to make the target sound. `NO_NOTE` when the sound itself is not in the table.

    `produced` is accepted and deliberately unused for the lookup: articulation belongs to
    the *target*, and the advice for making a /θ/ does not change with what came out
    instead. It stays in the signature because the accent feature will add bridging
    advice that does depend on both.
    """
    entry = lookup(expected)
    return entry.articulation if entry else NO_NOTE


def why_it_matters(expected: str | None, produced: str | None) -> str:
    """What the substitution costs a listener.

    Degrades to a statement of what Azure reported — a fact — rather than to an invented
    consequence. Saying "listeners will hear X" about a pair nobody has written up would
    be a confident guess dressed as coaching.
    """
    found = contrast(expected, produced)
    if found is not None:
        return found.why_it_matters
    if expected and produced:
        return f"Azure heard /{produced}/ where /{expected}/ was expected."
    return NO_NOTE


def minimal_pairs(expected: str | None, produced: str | None) -> list[tuple[str, str]]:
    """Word pairs differing only in this sound: (expected sound, produced sound).

    Empty when the pair is not in the table, and empty when the substitution genuinely has
    no minimal pair in English. Both are honest; inventing a pair would teach a word that
    does not exist.
    """
    found = contrast(expected, produced)
    return list(found.minimal_pairs) if found else []


def label_for(symbol: str | None) -> str:
    """A readable name for a sound, falling back to the symbol itself."""
    entry = lookup(symbol)
    return entry.label if entry else (normalise(symbol) or "?")
