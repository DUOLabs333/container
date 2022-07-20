import os
module_dict={}
module_dict["charset_normalizer"+os.sep+"legacy.py"]="""
import warnings
from typing import Dict, Optional, Union

from .api import from_bytes, from_fp, from_path, normalize
from .constant import CHARDET_CORRESPONDENCE
from .models import CharsetMatch, CharsetMatches


def detect(byte_str: bytes) -> Dict[str, Optional[Union[str, float]]]:
    \"\"\"
    chardet legacy method
    Detect the encoding of the given byte string. It should be mostly backward-compatible.
    Encoding name will match Chardet own writing whenever possible. (Not on encoding name unsupported by it)
    This function is deprecated and should be used to migrate your project easily, consult the documentation for
    further information. Not planned for removal.

    :param byte_str:     The byte sequence to examine.
    \"\"\"
    if not isinstance(byte_str, (bytearray, bytes)):
        raise TypeError(  # pragma: nocover
            \"Expected object of type bytes or bytearray, got: \"
            \"{0}\".format(type(byte_str))
        )

    if isinstance(byte_str, bytearray):
        byte_str = bytes(byte_str)

    r = from_bytes(byte_str).best()

    encoding = r.encoding if r is not None else None
    language = r.language if r is not None and r.language != \"Unknown\" else \"\"
    confidence = 1.0 - r.chaos if r is not None else None

    # Note: CharsetNormalizer does not return 'UTF-8-SIG' as the sig get stripped in the detection/normalization process
    # but chardet does return 'utf-8-sig' and it is a valid codec name.
    if r is not None and encoding == \"utf_8\" and r.bom:
        encoding += \"_sig\"

    return {
        \"encoding\": encoding
        if encoding not in CHARDET_CORRESPONDENCE
        else CHARDET_CORRESPONDENCE[encoding],
        \"language\": language,
        \"confidence\": confidence,
    }


class CharsetNormalizerMatch(CharsetMatch):
    pass


class CharsetNormalizerMatches(CharsetMatches):
    @staticmethod
    def from_fp(*args, **kwargs):  # type: ignore
        warnings.warn(  # pragma: nocover
            \"staticmethod from_fp, from_bytes, from_path and normalize are deprecated \"
            \"and scheduled to be removed in 3.0\",
            DeprecationWarning,
        )
        return from_fp(*args, **kwargs)  # pragma: nocover

    @staticmethod
    def from_bytes(*args, **kwargs):  # type: ignore
        warnings.warn(  # pragma: nocover
            \"staticmethod from_fp, from_bytes, from_path and normalize are deprecated \"
            \"and scheduled to be removed in 3.0\",
            DeprecationWarning,
        )
        return from_bytes(*args, **kwargs)  # pragma: nocover

    @staticmethod
    def from_path(*args, **kwargs):  # type: ignore
        warnings.warn(  # pragma: nocover
            \"staticmethod from_fp, from_bytes, from_path and normalize are deprecated \"
            \"and scheduled to be removed in 3.0\",
            DeprecationWarning,
        )
        return from_path(*args, **kwargs)  # pragma: nocover

    @staticmethod
    def normalize(*args, **kwargs):  # type: ignore
        warnings.warn(  # pragma: nocover
            \"staticmethod from_fp, from_bytes, from_path and normalize are deprecated \"
            \"and scheduled to be removed in 3.0\",
            DeprecationWarning,
        )
        return normalize(*args, **kwargs)  # pragma: nocover


class CharsetDetector(CharsetNormalizerMatches):
    pass


class CharsetDoctor(CharsetNormalizerMatches):
    pass

"""
module_dict["charset_normalizer"+os.sep+"constant.py"]="""
from codecs import BOM_UTF8, BOM_UTF16_BE, BOM_UTF16_LE, BOM_UTF32_BE, BOM_UTF32_LE
from encodings.aliases import aliases
from re import IGNORECASE, compile as re_compile
from typing import Dict, List, Set, Union

from .assets import FREQUENCIES

# Contain for each eligible encoding a list of/item bytes SIG/BOM
ENCODING_MARKS: Dict[str, Union[bytes, List[bytes]]] = {
    \"utf_8\": BOM_UTF8,
    \"utf_7\": [
        b\"\\x2b\\x2f\\x76\\x38\",
        b\"\\x2b\\x2f\\x76\\x39\",
        b\"\\x2b\\x2f\\x76\\x2b\",
        b\"\\x2b\\x2f\\x76\\x2f\",
        b\"\\x2b\\x2f\\x76\\x38\\x2d\",
    ],
    \"gb18030\": b\"\\x84\\x31\\x95\\x33\",
    \"utf_32\": [BOM_UTF32_BE, BOM_UTF32_LE],
    \"utf_16\": [BOM_UTF16_BE, BOM_UTF16_LE],
}

TOO_SMALL_SEQUENCE: int = 32
TOO_BIG_SEQUENCE: int = int(10e6)

UTF8_MAXIMAL_ALLOCATION: int = 1112064

UNICODE_RANGES_COMBINED: Dict[str, range] = {
    \"Control character\": range(31 + 1),
    \"Basic Latin\": range(32, 127 + 1),
    \"Latin-1 Supplement\": range(128, 255 + 1),
    \"Latin Extended-A\": range(256, 383 + 1),
    \"Latin Extended-B\": range(384, 591 + 1),
    \"IPA Extensions\": range(592, 687 + 1),
    \"Spacing Modifier Letters\": range(688, 767 + 1),
    \"Combining Diacritical Marks\": range(768, 879 + 1),
    \"Greek and Coptic\": range(880, 1023 + 1),
    \"Cyrillic\": range(1024, 1279 + 1),
    \"Cyrillic Supplement\": range(1280, 1327 + 1),
    \"Armenian\": range(1328, 1423 + 1),
    \"Hebrew\": range(1424, 1535 + 1),
    \"Arabic\": range(1536, 1791 + 1),
    \"Syriac\": range(1792, 1871 + 1),
    \"Arabic Supplement\": range(1872, 1919 + 1),
    \"Thaana\": range(1920, 1983 + 1),
    \"NKo\": range(1984, 2047 + 1),
    \"Samaritan\": range(2048, 2111 + 1),
    \"Mandaic\": range(2112, 2143 + 1),
    \"Syriac Supplement\": range(2144, 2159 + 1),
    \"Arabic Extended-A\": range(2208, 2303 + 1),
    \"Devanagari\": range(2304, 2431 + 1),
    \"Bengali\": range(2432, 2559 + 1),
    \"Gurmukhi\": range(2560, 2687 + 1),
    \"Gujarati\": range(2688, 2815 + 1),
    \"Oriya\": range(2816, 2943 + 1),
    \"Tamil\": range(2944, 3071 + 1),
    \"Telugu\": range(3072, 3199 + 1),
    \"Kannada\": range(3200, 3327 + 1),
    \"Malayalam\": range(3328, 3455 + 1),
    \"Sinhala\": range(3456, 3583 + 1),
    \"Thai\": range(3584, 3711 + 1),
    \"Lao\": range(3712, 3839 + 1),
    \"Tibetan\": range(3840, 4095 + 1),
    \"Myanmar\": range(4096, 4255 + 1),
    \"Georgian\": range(4256, 4351 + 1),
    \"Hangul Jamo\": range(4352, 4607 + 1),
    \"Ethiopic\": range(4608, 4991 + 1),
    \"Ethiopic Supplement\": range(4992, 5023 + 1),
    \"Cherokee\": range(5024, 5119 + 1),
    \"Unified Canadian Aboriginal Syllabics\": range(5120, 5759 + 1),
    \"Ogham\": range(5760, 5791 + 1),
    \"Runic\": range(5792, 5887 + 1),
    \"Tagalog\": range(5888, 5919 + 1),
    \"Hanunoo\": range(5920, 5951 + 1),
    \"Buhid\": range(5952, 5983 + 1),
    \"Tagbanwa\": range(5984, 6015 + 1),
    \"Khmer\": range(6016, 6143 + 1),
    \"Mongolian\": range(6144, 6319 + 1),
    \"Unified Canadian Aboriginal Syllabics Extended\": range(6320, 6399 + 1),
    \"Limbu\": range(6400, 6479 + 1),
    \"Tai Le\": range(6480, 6527 + 1),
    \"New Tai Lue\": range(6528, 6623 + 1),
    \"Khmer Symbols\": range(6624, 6655 + 1),
    \"Buginese\": range(6656, 6687 + 1),
    \"Tai Tham\": range(6688, 6831 + 1),
    \"Combining Diacritical Marks Extended\": range(6832, 6911 + 1),
    \"Balinese\": range(6912, 7039 + 1),
    \"Sundanese\": range(7040, 7103 + 1),
    \"Batak\": range(7104, 7167 + 1),
    \"Lepcha\": range(7168, 7247 + 1),
    \"Ol Chiki\": range(7248, 7295 + 1),
    \"Cyrillic Extended C\": range(7296, 7311 + 1),
    \"Sundanese Supplement\": range(7360, 7375 + 1),
    \"Vedic Extensions\": range(7376, 7423 + 1),
    \"Phonetic Extensions\": range(7424, 7551 + 1),
    \"Phonetic Extensions Supplement\": range(7552, 7615 + 1),
    \"Combining Diacritical Marks Supplement\": range(7616, 7679 + 1),
    \"Latin Extended Additional\": range(7680, 7935 + 1),
    \"Greek Extended\": range(7936, 8191 + 1),
    \"General Punctuation\": range(8192, 8303 + 1),
    \"Superscripts and Subscripts\": range(8304, 8351 + 1),
    \"Currency Symbols\": range(8352, 8399 + 1),
    \"Combining Diacritical Marks for Symbols\": range(8400, 8447 + 1),
    \"Letterlike Symbols\": range(8448, 8527 + 1),
    \"Number Forms\": range(8528, 8591 + 1),
    \"Arrows\": range(8592, 8703 + 1),
    \"Mathematical Operators\": range(8704, 8959 + 1),
    \"Miscellaneous Technical\": range(8960, 9215 + 1),
    \"Control Pictures\": range(9216, 9279 + 1),
    \"Optical Character Recognition\": range(9280, 9311 + 1),
    \"Enclosed Alphanumerics\": range(9312, 9471 + 1),
    \"Box Drawing\": range(9472, 9599 + 1),
    \"Block Elements\": range(9600, 9631 + 1),
    \"Geometric Shapes\": range(9632, 9727 + 1),
    \"Miscellaneous Symbols\": range(9728, 9983 + 1),
    \"Dingbats\": range(9984, 10175 + 1),
    \"Miscellaneous Mathematical Symbols-A\": range(10176, 10223 + 1),
    \"Supplemental Arrows-A\": range(10224, 10239 + 1),
    \"Braille Patterns\": range(10240, 10495 + 1),
    \"Supplemental Arrows-B\": range(10496, 10623 + 1),
    \"Miscellaneous Mathematical Symbols-B\": range(10624, 10751 + 1),
    \"Supplemental Mathematical Operators\": range(10752, 11007 + 1),
    \"Miscellaneous Symbols and Arrows\": range(11008, 11263 + 1),
    \"Glagolitic\": range(11264, 11359 + 1),
    \"Latin Extended-C\": range(11360, 11391 + 1),
    \"Coptic\": range(11392, 11519 + 1),
    \"Georgian Supplement\": range(11520, 11567 + 1),
    \"Tifinagh\": range(11568, 11647 + 1),
    \"Ethiopic Extended\": range(11648, 11743 + 1),
    \"Cyrillic Extended-A\": range(11744, 11775 + 1),
    \"Supplemental Punctuation\": range(11776, 11903 + 1),
    \"CJK Radicals Supplement\": range(11904, 12031 + 1),
    \"Kangxi Radicals\": range(12032, 12255 + 1),
    \"Ideographic Description Characters\": range(12272, 12287 + 1),
    \"CJK Symbols and Punctuation\": range(12288, 12351 + 1),
    \"Hiragana\": range(12352, 12447 + 1),
    \"Katakana\": range(12448, 12543 + 1),
    \"Bopomofo\": range(12544, 12591 + 1),
    \"Hangul Compatibility Jamo\": range(12592, 12687 + 1),
    \"Kanbun\": range(12688, 12703 + 1),
    \"Bopomofo Extended\": range(12704, 12735 + 1),
    \"CJK Strokes\": range(12736, 12783 + 1),
    \"Katakana Phonetic Extensions\": range(12784, 12799 + 1),
    \"Enclosed CJK Letters and Months\": range(12800, 13055 + 1),
    \"CJK Compatibility\": range(13056, 13311 + 1),
    \"CJK Unified Ideographs Extension A\": range(13312, 19903 + 1),
    \"Yijing Hexagram Symbols\": range(19904, 19967 + 1),
    \"CJK Unified Ideographs\": range(19968, 40959 + 1),
    \"Yi Syllables\": range(40960, 42127 + 1),
    \"Yi Radicals\": range(42128, 42191 + 1),
    \"Lisu\": range(42192, 42239 + 1),
    \"Vai\": range(42240, 42559 + 1),
    \"Cyrillic Extended-B\": range(42560, 42655 + 1),
    \"Bamum\": range(42656, 42751 + 1),
    \"Modifier Tone Letters\": range(42752, 42783 + 1),
    \"Latin Extended-D\": range(42784, 43007 + 1),
    \"Syloti Nagri\": range(43008, 43055 + 1),
    \"Common Indic Number Forms\": range(43056, 43071 + 1),
    \"Phags-pa\": range(43072, 43135 + 1),
    \"Saurashtra\": range(43136, 43231 + 1),
    \"Devanagari Extended\": range(43232, 43263 + 1),
    \"Kayah Li\": range(43264, 43311 + 1),
    \"Rejang\": range(43312, 43359 + 1),
    \"Hangul Jamo Extended-A\": range(43360, 43391 + 1),
    \"Javanese\": range(43392, 43487 + 1),
    \"Myanmar Extended-B\": range(43488, 43519 + 1),
    \"Cham\": range(43520, 43615 + 1),
    \"Myanmar Extended-A\": range(43616, 43647 + 1),
    \"Tai Viet\": range(43648, 43743 + 1),
    \"Meetei Mayek Extensions\": range(43744, 43775 + 1),
    \"Ethiopic Extended-A\": range(43776, 43823 + 1),
    \"Latin Extended-E\": range(43824, 43887 + 1),
    \"Cherokee Supplement\": range(43888, 43967 + 1),
    \"Meetei Mayek\": range(43968, 44031 + 1),
    \"Hangul Syllables\": range(44032, 55215 + 1),
    \"Hangul Jamo Extended-B\": range(55216, 55295 + 1),
    \"High Surrogates\": range(55296, 56191 + 1),
    \"High Private Use Surrogates\": range(56192, 56319 + 1),
    \"Low Surrogates\": range(56320, 57343 + 1),
    \"Private Use Area\": range(57344, 63743 + 1),
    \"CJK Compatibility Ideographs\": range(63744, 64255 + 1),
    \"Alphabetic Presentation Forms\": range(64256, 64335 + 1),
    \"Arabic Presentation Forms-A\": range(64336, 65023 + 1),
    \"Variation Selectors\": range(65024, 65039 + 1),
    \"Vertical Forms\": range(65040, 65055 + 1),
    \"Combining Half Marks\": range(65056, 65071 + 1),
    \"CJK Compatibility Forms\": range(65072, 65103 + 1),
    \"Small Form Variants\": range(65104, 65135 + 1),
    \"Arabic Presentation Forms-B\": range(65136, 65279 + 1),
    \"Halfwidth and Fullwidth Forms\": range(65280, 65519 + 1),
    \"Specials\": range(65520, 65535 + 1),
    \"Linear B Syllabary\": range(65536, 65663 + 1),
    \"Linear B Ideograms\": range(65664, 65791 + 1),
    \"Aegean Numbers\": range(65792, 65855 + 1),
    \"Ancient Greek Numbers\": range(65856, 65935 + 1),
    \"Ancient Symbols\": range(65936, 65999 + 1),
    \"Phaistos Disc\": range(66000, 66047 + 1),
    \"Lycian\": range(66176, 66207 + 1),
    \"Carian\": range(66208, 66271 + 1),
    \"Coptic Epact Numbers\": range(66272, 66303 + 1),
    \"Old Italic\": range(66304, 66351 + 1),
    \"Gothic\": range(66352, 66383 + 1),
    \"Old Permic\": range(66384, 66431 + 1),
    \"Ugaritic\": range(66432, 66463 + 1),
    \"Old Persian\": range(66464, 66527 + 1),
    \"Deseret\": range(66560, 66639 + 1),
    \"Shavian\": range(66640, 66687 + 1),
    \"Osmanya\": range(66688, 66735 + 1),
    \"Osage\": range(66736, 66815 + 1),
    \"Elbasan\": range(66816, 66863 + 1),
    \"Caucasian Albanian\": range(66864, 66927 + 1),
    \"Linear A\": range(67072, 67455 + 1),
    \"Cypriot Syllabary\": range(67584, 67647 + 1),
    \"Imperial Aramaic\": range(67648, 67679 + 1),
    \"Palmyrene\": range(67680, 67711 + 1),
    \"Nabataean\": range(67712, 67759 + 1),
    \"Hatran\": range(67808, 67839 + 1),
    \"Phoenician\": range(67840, 67871 + 1),
    \"Lydian\": range(67872, 67903 + 1),
    \"Meroitic Hieroglyphs\": range(67968, 67999 + 1),
    \"Meroitic Cursive\": range(68000, 68095 + 1),
    \"Kharoshthi\": range(68096, 68191 + 1),
    \"Old South Arabian\": range(68192, 68223 + 1),
    \"Old North Arabian\": range(68224, 68255 + 1),
    \"Manichaean\": range(68288, 68351 + 1),
    \"Avestan\": range(68352, 68415 + 1),
    \"Inscriptional Parthian\": range(68416, 68447 + 1),
    \"Inscriptional Pahlavi\": range(68448, 68479 + 1),
    \"Psalter Pahlavi\": range(68480, 68527 + 1),
    \"Old Turkic\": range(68608, 68687 + 1),
    \"Old Hungarian\": range(68736, 68863 + 1),
    \"Rumi Numeral Symbols\": range(69216, 69247 + 1),
    \"Brahmi\": range(69632, 69759 + 1),
    \"Kaithi\": range(69760, 69839 + 1),
    \"Sora Sompeng\": range(69840, 69887 + 1),
    \"Chakma\": range(69888, 69967 + 1),
    \"Mahajani\": range(69968, 70015 + 1),
    \"Sharada\": range(70016, 70111 + 1),
    \"Sinhala Archaic Numbers\": range(70112, 70143 + 1),
    \"Khojki\": range(70144, 70223 + 1),
    \"Multani\": range(70272, 70319 + 1),
    \"Khudawadi\": range(70320, 70399 + 1),
    \"Grantha\": range(70400, 70527 + 1),
    \"Newa\": range(70656, 70783 + 1),
    \"Tirhuta\": range(70784, 70879 + 1),
    \"Siddham\": range(71040, 71167 + 1),
    \"Modi\": range(71168, 71263 + 1),
    \"Mongolian Supplement\": range(71264, 71295 + 1),
    \"Takri\": range(71296, 71375 + 1),
    \"Ahom\": range(71424, 71487 + 1),
    \"Warang Citi\": range(71840, 71935 + 1),
    \"Zanabazar Square\": range(72192, 72271 + 1),
    \"Soyombo\": range(72272, 72367 + 1),
    \"Pau Cin Hau\": range(72384, 72447 + 1),
    \"Bhaiksuki\": range(72704, 72815 + 1),
    \"Marchen\": range(72816, 72895 + 1),
    \"Masaram Gondi\": range(72960, 73055 + 1),
    \"Cuneiform\": range(73728, 74751 + 1),
    \"Cuneiform Numbers and Punctuation\": range(74752, 74879 + 1),
    \"Early Dynastic Cuneiform\": range(74880, 75087 + 1),
    \"Egyptian Hieroglyphs\": range(77824, 78895 + 1),
    \"Anatolian Hieroglyphs\": range(82944, 83583 + 1),
    \"Bamum Supplement\": range(92160, 92735 + 1),
    \"Mro\": range(92736, 92783 + 1),
    \"Bassa Vah\": range(92880, 92927 + 1),
    \"Pahawh Hmong\": range(92928, 93071 + 1),
    \"Miao\": range(93952, 94111 + 1),
    \"Ideographic Symbols and Punctuation\": range(94176, 94207 + 1),
    \"Tangut\": range(94208, 100351 + 1),
    \"Tangut Components\": range(100352, 101119 + 1),
    \"Kana Supplement\": range(110592, 110847 + 1),
    \"Kana Extended-A\": range(110848, 110895 + 1),
    \"Nushu\": range(110960, 111359 + 1),
    \"Duployan\": range(113664, 113823 + 1),
    \"Shorthand Format Controls\": range(113824, 113839 + 1),
    \"Byzantine Musical Symbols\": range(118784, 119039 + 1),
    \"Musical Symbols\": range(119040, 119295 + 1),
    \"Ancient Greek Musical Notation\": range(119296, 119375 + 1),
    \"Tai Xuan Jing Symbols\": range(119552, 119647 + 1),
    \"Counting Rod Numerals\": range(119648, 119679 + 1),
    \"Mathematical Alphanumeric Symbols\": range(119808, 120831 + 1),
    \"Sutton SignWriting\": range(120832, 121519 + 1),
    \"Glagolitic Supplement\": range(122880, 122927 + 1),
    \"Mende Kikakui\": range(124928, 125151 + 1),
    \"Adlam\": range(125184, 125279 + 1),
    \"Arabic Mathematical Alphabetic Symbols\": range(126464, 126719 + 1),
    \"Mahjong Tiles\": range(126976, 127023 + 1),
    \"Domino Tiles\": range(127024, 127135 + 1),
    \"Playing Cards\": range(127136, 127231 + 1),
    \"Enclosed Alphanumeric Supplement\": range(127232, 127487 + 1),
    \"Enclosed Ideographic Supplement\": range(127488, 127743 + 1),
    \"Miscellaneous Symbols and Pictographs\": range(127744, 128511 + 1),
    \"Emoticons range(Emoji)\": range(128512, 128591 + 1),
    \"Ornamental Dingbats\": range(128592, 128639 + 1),
    \"Transport and Map Symbols\": range(128640, 128767 + 1),
    \"Alchemical Symbols\": range(128768, 128895 + 1),
    \"Geometric Shapes Extended\": range(128896, 129023 + 1),
    \"Supplemental Arrows-C\": range(129024, 129279 + 1),
    \"Supplemental Symbols and Pictographs\": range(129280, 129535 + 1),
    \"CJK Unified Ideographs Extension B\": range(131072, 173791 + 1),
    \"CJK Unified Ideographs Extension C\": range(173824, 177983 + 1),
    \"CJK Unified Ideographs Extension D\": range(177984, 178207 + 1),
    \"CJK Unified Ideographs Extension E\": range(178208, 183983 + 1),
    \"CJK Unified Ideographs Extension F\": range(183984, 191471 + 1),
    \"CJK Compatibility Ideographs Supplement\": range(194560, 195103 + 1),
    \"Tags\": range(917504, 917631 + 1),
    \"Variation Selectors Supplement\": range(917760, 917999 + 1),
}


UNICODE_SECONDARY_RANGE_KEYWORD: List[str] = [
    \"Supplement\",
    \"Extended\",
    \"Extensions\",
    \"Modifier\",
    \"Marks\",
    \"Punctuation\",
    \"Symbols\",
    \"Forms\",
    \"Operators\",
    \"Miscellaneous\",
    \"Drawing\",
    \"Block\",
    \"Shapes\",
    \"Supplemental\",
    \"Tags\",
]

RE_POSSIBLE_ENCODING_INDICATION = re_compile(
    r\"(?:(?:encoding)|(?:charset)|(?:coding))(?:[\\:= ]{1,10})(?:[\\\"\\']?)([a-zA-Z0-9\\-_]+)(?:[\\\"\\']?)\",
    IGNORECASE,
)

IANA_SUPPORTED: List[str] = sorted(
    filter(
        lambda x: x.endswith(\"_codec\") is False
        and x not in {\"rot_13\", \"tactis\", \"mbcs\"},
        list(set(aliases.values())),
    )
)

IANA_SUPPORTED_COUNT: int = len(IANA_SUPPORTED)

# pre-computed code page that are similar using the function cp_similarity.
IANA_SUPPORTED_SIMILAR: Dict[str, List[str]] = {
    \"cp037\": [\"cp1026\", \"cp1140\", \"cp273\", \"cp500\"],
    \"cp1026\": [\"cp037\", \"cp1140\", \"cp273\", \"cp500\"],
    \"cp1125\": [\"cp866\"],
    \"cp1140\": [\"cp037\", \"cp1026\", \"cp273\", \"cp500\"],
    \"cp1250\": [\"iso8859_2\"],
    \"cp1251\": [\"kz1048\", \"ptcp154\"],
    \"cp1252\": [\"iso8859_15\", \"iso8859_9\", \"latin_1\"],
    \"cp1253\": [\"iso8859_7\"],
    \"cp1254\": [\"iso8859_15\", \"iso8859_9\", \"latin_1\"],
    \"cp1257\": [\"iso8859_13\"],
    \"cp273\": [\"cp037\", \"cp1026\", \"cp1140\", \"cp500\"],
    \"cp437\": [\"cp850\", \"cp858\", \"cp860\", \"cp861\", \"cp862\", \"cp863\", \"cp865\"],
    \"cp500\": [\"cp037\", \"cp1026\", \"cp1140\", \"cp273\"],
    \"cp850\": [\"cp437\", \"cp857\", \"cp858\", \"cp865\"],
    \"cp857\": [\"cp850\", \"cp858\", \"cp865\"],
    \"cp858\": [\"cp437\", \"cp850\", \"cp857\", \"cp865\"],
    \"cp860\": [\"cp437\", \"cp861\", \"cp862\", \"cp863\", \"cp865\"],
    \"cp861\": [\"cp437\", \"cp860\", \"cp862\", \"cp863\", \"cp865\"],
    \"cp862\": [\"cp437\", \"cp860\", \"cp861\", \"cp863\", \"cp865\"],
    \"cp863\": [\"cp437\", \"cp860\", \"cp861\", \"cp862\", \"cp865\"],
    \"cp865\": [\"cp437\", \"cp850\", \"cp857\", \"cp858\", \"cp860\", \"cp861\", \"cp862\", \"cp863\"],
    \"cp866\": [\"cp1125\"],
    \"iso8859_10\": [\"iso8859_14\", \"iso8859_15\", \"iso8859_4\", \"iso8859_9\", \"latin_1\"],
    \"iso8859_11\": [\"tis_620\"],
    \"iso8859_13\": [\"cp1257\"],
    \"iso8859_14\": [
        \"iso8859_10\",
        \"iso8859_15\",
        \"iso8859_16\",
        \"iso8859_3\",
        \"iso8859_9\",
        \"latin_1\",
    ],
    \"iso8859_15\": [
        \"cp1252\",
        \"cp1254\",
        \"iso8859_10\",
        \"iso8859_14\",
        \"iso8859_16\",
        \"iso8859_3\",
        \"iso8859_9\",
        \"latin_1\",
    ],
    \"iso8859_16\": [
        \"iso8859_14\",
        \"iso8859_15\",
        \"iso8859_2\",
        \"iso8859_3\",
        \"iso8859_9\",
        \"latin_1\",
    ],
    \"iso8859_2\": [\"cp1250\", \"iso8859_16\", \"iso8859_4\"],
    \"iso8859_3\": [\"iso8859_14\", \"iso8859_15\", \"iso8859_16\", \"iso8859_9\", \"latin_1\"],
    \"iso8859_4\": [\"iso8859_10\", \"iso8859_2\", \"iso8859_9\", \"latin_1\"],
    \"iso8859_7\": [\"cp1253\"],
    \"iso8859_9\": [
        \"cp1252\",
        \"cp1254\",
        \"cp1258\",
        \"iso8859_10\",
        \"iso8859_14\",
        \"iso8859_15\",
        \"iso8859_16\",
        \"iso8859_3\",
        \"iso8859_4\",
        \"latin_1\",
    ],
    \"kz1048\": [\"cp1251\", \"ptcp154\"],
    \"latin_1\": [
        \"cp1252\",
        \"cp1254\",
        \"cp1258\",
        \"iso8859_10\",
        \"iso8859_14\",
        \"iso8859_15\",
        \"iso8859_16\",
        \"iso8859_3\",
        \"iso8859_4\",
        \"iso8859_9\",
    ],
    \"mac_iceland\": [\"mac_roman\", \"mac_turkish\"],
    \"mac_roman\": [\"mac_iceland\", \"mac_turkish\"],
    \"mac_turkish\": [\"mac_iceland\", \"mac_roman\"],
    \"ptcp154\": [\"cp1251\", \"kz1048\"],
    \"tis_620\": [\"iso8859_11\"],
}


CHARDET_CORRESPONDENCE: Dict[str, str] = {
    \"iso2022_kr\": \"ISO-2022-KR\",
    \"iso2022_jp\": \"ISO-2022-JP\",
    \"euc_kr\": \"EUC-KR\",
    \"tis_620\": \"TIS-620\",
    \"utf_32\": \"UTF-32\",
    \"euc_jp\": \"EUC-JP\",
    \"koi8_r\": \"KOI8-R\",
    \"iso8859_1\": \"ISO-8859-1\",
    \"iso8859_2\": \"ISO-8859-2\",
    \"iso8859_5\": \"ISO-8859-5\",
    \"iso8859_6\": \"ISO-8859-6\",
    \"iso8859_7\": \"ISO-8859-7\",
    \"iso8859_8\": \"ISO-8859-8\",
    \"utf_16\": \"UTF-16\",
    \"cp855\": \"IBM855\",
    \"mac_cyrillic\": \"MacCyrillic\",
    \"gb2312\": \"GB2312\",
    \"gb18030\": \"GB18030\",
    \"cp932\": \"CP932\",
    \"cp866\": \"IBM866\",
    \"utf_8\": \"utf-8\",
    \"utf_8_sig\": \"UTF-8-SIG\",
    \"shift_jis\": \"SHIFT_JIS\",
    \"big5\": \"Big5\",
    \"cp1250\": \"windows-1250\",
    \"cp1251\": \"windows-1251\",
    \"cp1252\": \"Windows-1252\",
    \"cp1253\": \"windows-1253\",
    \"cp1255\": \"windows-1255\",
    \"cp1256\": \"windows-1256\",
    \"cp1254\": \"Windows-1254\",
    \"cp949\": \"CP949\",
}


COMMON_SAFE_ASCII_CHARACTERS: Set[str] = {
    \"<\",
    \">\",
    \"=\",
    \":\",
    \"/\",
    \"&\",
    \";\",
    \"{\",
    \"}\",
    \"[\",
    \"]\",
    \",\",
    \"|\",
    '\"',
    \"-\",
}


KO_NAMES: Set[str] = {\"johab\", \"cp949\", \"euc_kr\"}
ZH_NAMES: Set[str] = {\"big5\", \"cp950\", \"big5hkscs\", \"hz\"}

NOT_PRINTABLE_PATTERN = re_compile(r\"[0-9\\W\\n\\r\\t]+\")

LANGUAGE_SUPPORTED_COUNT: int = len(FREQUENCIES)

# Logging LEVEL bellow DEBUG
TRACE: int = 5

"""
module_dict["charset_normalizer"+os.sep+"models.py"]="""
import warnings
from collections import Counter
from encodings.aliases import aliases
from hashlib import sha256
from json import dumps
from re import sub
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

from .constant import NOT_PRINTABLE_PATTERN, TOO_BIG_SEQUENCE
from .md import mess_ratio
from .utils import iana_name, is_multi_byte_encoding, unicode_range


class CharsetMatch:
    def __init__(
        self,
        payload: bytes,
        guessed_encoding: str,
        mean_mess_ratio: float,
        has_sig_or_bom: bool,
        languages: \"CoherenceMatches\",
        decoded_payload: Optional[str] = None,
    ):
        self._payload: bytes = payload

        self._encoding: str = guessed_encoding
        self._mean_mess_ratio: float = mean_mess_ratio
        self._languages: CoherenceMatches = languages
        self._has_sig_or_bom: bool = has_sig_or_bom
        self._unicode_ranges: Optional[List[str]] = None

        self._leaves: List[CharsetMatch] = []
        self._mean_coherence_ratio: float = 0.0

        self._output_payload: Optional[bytes] = None
        self._output_encoding: Optional[str] = None

        self._string: Optional[str] = decoded_payload

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, CharsetMatch):
            raise TypeError(
                \"__eq__ cannot be invoked on {} and {}.\".format(
                    str(other.__class__), str(self.__class__)
                )
            )
        return self.encoding == other.encoding and self.fingerprint == other.fingerprint

    def __lt__(self, other: object) -> bool:
        \"\"\"
        Implemented to make sorted available upon CharsetMatches items.
        \"\"\"
        if not isinstance(other, CharsetMatch):
            raise ValueError

        chaos_difference: float = abs(self.chaos - other.chaos)
        coherence_difference: float = abs(self.coherence - other.coherence)

        # Bellow 1% difference --> Use Coherence
        if chaos_difference < 0.01 and coherence_difference > 0.02:
            # When having a tough decision, use the result that decoded as many multi-byte as possible.
            if chaos_difference == 0.0 and self.coherence == other.coherence:
                return self.multi_byte_usage > other.multi_byte_usage
            return self.coherence > other.coherence

        return self.chaos < other.chaos

    @property
    def multi_byte_usage(self) -> float:
        return 1.0 - len(str(self)) / len(self.raw)

    @property
    def chaos_secondary_pass(self) -> float:
        \"\"\"
        Check once again chaos in decoded text, except this time, with full content.
        Use with caution, this can be very slow.
        Notice: Will be removed in 3.0
        \"\"\"
        warnings.warn(
            \"chaos_secondary_pass is deprecated and will be removed in 3.0\",
            DeprecationWarning,
        )
        return mess_ratio(str(self), 1.0)

    @property
    def coherence_non_latin(self) -> float:
        \"\"\"
        Coherence ratio on the first non-latin language detected if ANY.
        Notice: Will be removed in 3.0
        \"\"\"
        warnings.warn(
            \"coherence_non_latin is deprecated and will be removed in 3.0\",
            DeprecationWarning,
        )
        return 0.0

    @property
    def w_counter(self) -> Counter:
        \"\"\"
        Word counter instance on decoded text.
        Notice: Will be removed in 3.0
        \"\"\"
        warnings.warn(
            \"w_counter is deprecated and will be removed in 3.0\", DeprecationWarning
        )

        string_printable_only = sub(NOT_PRINTABLE_PATTERN, \" \", str(self).lower())

        return Counter(string_printable_only.split())

    def __str__(self) -> str:
        # Lazy Str Loading
        if self._string is None:
            self._string = str(self._payload, self._encoding, \"strict\")
        return self._string

    def __repr__(self) -> str:
        return \"<CharsetMatch '{}' bytes({})>\".format(self.encoding, self.fingerprint)

    def add_submatch(self, other: \"CharsetMatch\") -> None:
        if not isinstance(other, CharsetMatch) or other == self:
            raise ValueError(
                \"Unable to add instance <{}> as a submatch of a CharsetMatch\".format(
                    other.__class__
                )
            )

        other._string = None  # Unload RAM usage; dirty trick.
        self._leaves.append(other)

    @property
    def encoding(self) -> str:
        return self._encoding

    @property
    def encoding_aliases(self) -> List[str]:
        \"\"\"
        Encoding name are known by many name, using this could help when searching for IBM855 when it's listed as CP855.
        \"\"\"
        also_known_as: List[str] = []
        for u, p in aliases.items():
            if self.encoding == u:
                also_known_as.append(p)
            elif self.encoding == p:
                also_known_as.append(u)
        return also_known_as

    @property
    def bom(self) -> bool:
        return self._has_sig_or_bom

    @property
    def byte_order_mark(self) -> bool:
        return self._has_sig_or_bom

    @property
    def languages(self) -> List[str]:
        \"\"\"
        Return the complete list of possible languages found in decoded sequence.
        Usually not really useful. Returned list may be empty even if 'language' property return something != 'Unknown'.
        \"\"\"
        return [e[0] for e in self._languages]

    @property
    def language(self) -> str:
        \"\"\"
        Most probable language found in decoded sequence. If none were detected or inferred, the property will return
        \"Unknown\".
        \"\"\"
        if not self._languages:
            # Trying to infer the language based on the given encoding
            # Its either English or we should not pronounce ourselves in certain cases.
            if \"ascii\" in self.could_be_from_charset:
                return \"English\"

            # doing it there to avoid circular import
            from charset_normalizer.cd import encoding_languages, mb_encoding_languages

            languages = (
                mb_encoding_languages(self.encoding)
                if is_multi_byte_encoding(self.encoding)
                else encoding_languages(self.encoding)
            )

            if len(languages) == 0 or \"Latin Based\" in languages:
                return \"Unknown\"

            return languages[0]

        return self._languages[0][0]

    @property
    def chaos(self) -> float:
        return self._mean_mess_ratio

    @property
    def coherence(self) -> float:
        if not self._languages:
            return 0.0
        return self._languages[0][1]

    @property
    def percent_chaos(self) -> float:
        return round(self.chaos * 100, ndigits=3)

    @property
    def percent_coherence(self) -> float:
        return round(self.coherence * 100, ndigits=3)

    @property
    def raw(self) -> bytes:
        \"\"\"
        Original untouched bytes.
        \"\"\"
        return self._payload

    @property
    def submatch(self) -> List[\"CharsetMatch\"]:
        return self._leaves

    @property
    def has_submatch(self) -> bool:
        return len(self._leaves) > 0

    @property
    def alphabets(self) -> List[str]:
        if self._unicode_ranges is not None:
            return self._unicode_ranges
        # list detected ranges
        detected_ranges: List[Optional[str]] = [
            unicode_range(char) for char in str(self)
        ]
        # filter and sort
        self._unicode_ranges = sorted(list({r for r in detected_ranges if r}))
        return self._unicode_ranges

    @property
    def could_be_from_charset(self) -> List[str]:
        \"\"\"
        The complete list of encoding that output the exact SAME str result and therefore could be the originating
        encoding.
        This list does include the encoding available in property 'encoding'.
        \"\"\"
        return [self._encoding] + [m.encoding for m in self._leaves]

    def first(self) -> \"CharsetMatch\":
        \"\"\"
        Kept for BC reasons. Will be removed in 3.0.
        \"\"\"
        return self

    def best(self) -> \"CharsetMatch\":
        \"\"\"
        Kept for BC reasons. Will be removed in 3.0.
        \"\"\"
        return self

    def output(self, encoding: str = \"utf_8\") -> bytes:
        \"\"\"
        Method to get re-encoded bytes payload using given target encoding. Default to UTF-8.
        Any errors will be simply ignored by the encoder NOT replaced.
        \"\"\"
        if self._output_encoding is None or self._output_encoding != encoding:
            self._output_encoding = encoding
            self._output_payload = str(self).encode(encoding, \"replace\")

        return self._output_payload  # type: ignore

    @property
    def fingerprint(self) -> str:
        \"\"\"
        Retrieve the unique SHA256 computed using the transformed (re-encoded) payload. Not the original one.
        \"\"\"
        return sha256(self.output()).hexdigest()


class CharsetMatches:
    \"\"\"
    Container with every CharsetMatch items ordered by default from most probable to the less one.
    Act like a list(iterable) but does not implements all related methods.
    \"\"\"

    def __init__(self, results: List[CharsetMatch] = None):
        self._results: List[CharsetMatch] = sorted(results) if results else []

    def __iter__(self) -> Iterator[CharsetMatch]:
        yield from self._results

    def __getitem__(self, item: Union[int, str]) -> CharsetMatch:
        \"\"\"
        Retrieve a single item either by its position or encoding name (alias may be used here).
        Raise KeyError upon invalid index or encoding not present in results.
        \"\"\"
        if isinstance(item, int):
            return self._results[item]
        if isinstance(item, str):
            item = iana_name(item, False)
            for result in self._results:
                if item in result.could_be_from_charset:
                    return result
        raise KeyError

    def __len__(self) -> int:
        return len(self._results)

    def __bool__(self) -> bool:
        return len(self._results) > 0

    def append(self, item: CharsetMatch) -> None:
        \"\"\"
        Insert a single match. Will be inserted accordingly to preserve sort.
        Can be inserted as a submatch.
        \"\"\"
        if not isinstance(item, CharsetMatch):
            raise ValueError(
                \"Cannot append instance '{}' to CharsetMatches\".format(
                    str(item.__class__)
                )
            )
        # We should disable the submatch factoring when the input file is too heavy (conserve RAM usage)
        if len(item.raw) <= TOO_BIG_SEQUENCE:
            for match in self._results:
                if match.fingerprint == item.fingerprint and match.chaos == item.chaos:
                    match.add_submatch(item)
                    return
        self._results.append(item)
        self._results = sorted(self._results)

    def best(self) -> Optional[\"CharsetMatch\"]:
        \"\"\"
        Simply return the first match. Strict equivalent to matches[0].
        \"\"\"
        if not self._results:
            return None
        return self._results[0]

    def first(self) -> Optional[\"CharsetMatch\"]:
        \"\"\"
        Redundant method, call the method best(). Kept for BC reasons.
        \"\"\"
        return self.best()


CoherenceMatch = Tuple[str, float]
CoherenceMatches = List[CoherenceMatch]


class CliDetectionResult:
    def __init__(
        self,
        path: str,
        encoding: Optional[str],
        encoding_aliases: List[str],
        alternative_encodings: List[str],
        language: str,
        alphabets: List[str],
        has_sig_or_bom: bool,
        chaos: float,
        coherence: float,
        unicode_path: Optional[str],
        is_preferred: bool,
    ):
        self.path: str = path
        self.unicode_path: Optional[str] = unicode_path
        self.encoding: Optional[str] = encoding
        self.encoding_aliases: List[str] = encoding_aliases
        self.alternative_encodings: List[str] = alternative_encodings
        self.language: str = language
        self.alphabets: List[str] = alphabets
        self.has_sig_or_bom: bool = has_sig_or_bom
        self.chaos: float = chaos
        self.coherence: float = coherence
        self.is_preferred: bool = is_preferred

    @property
    def __dict__(self) -> Dict[str, Any]:  # type: ignore
        return {
            \"path\": self.path,
            \"encoding\": self.encoding,
            \"encoding_aliases\": self.encoding_aliases,
            \"alternative_encodings\": self.alternative_encodings,
            \"language\": self.language,
            \"alphabets\": self.alphabets,
            \"has_sig_or_bom\": self.has_sig_or_bom,
            \"chaos\": self.chaos,
            \"coherence\": self.coherence,
            \"unicode_path\": self.unicode_path,
            \"is_preferred\": self.is_preferred,
        }

    def to_json(self) -> str:
        return dumps(self.__dict__, ensure_ascii=True, indent=4)

"""
module_dict["charset_normalizer"+os.sep+"api.py"]="""
import logging
from os import PathLike
from os.path import basename, splitext
from typing import BinaryIO, List, Optional, Set

from .cd import (
    coherence_ratio,
    encoding_languages,
    mb_encoding_languages,
    merge_coherence_ratios,
)
from .constant import IANA_SUPPORTED, TOO_BIG_SEQUENCE, TOO_SMALL_SEQUENCE, TRACE
from .md import mess_ratio
from .models import CharsetMatch, CharsetMatches
from .utils import (
    any_specified_encoding,
    cut_sequence_chunks,
    iana_name,
    identify_sig_or_bom,
    is_cp_similar,
    is_multi_byte_encoding,
    should_strip_sig_or_bom,
)

# Will most likely be controversial
# logging.addLevelName(TRACE, \"TRACE\")
logger = logging.getLogger(\"charset_normalizer\")
explain_handler = logging.StreamHandler()
explain_handler.setFormatter(
    logging.Formatter(\"%(asctime)s | %(levelname)s | %(message)s\")
)


def from_bytes(
    sequences: bytes,
    steps: int = 5,
    chunk_size: int = 512,
    threshold: float = 0.2,
    cp_isolation: List[str] = None,
    cp_exclusion: List[str] = None,
    preemptive_behaviour: bool = True,
    explain: bool = False,
) -> CharsetMatches:
    \"\"\"
    Given a raw bytes sequence, return the best possibles charset usable to render str objects.
    If there is no results, it is a strong indicator that the source is binary/not text.
    By default, the process will extract 5 blocs of 512o each to assess the mess and coherence of a given sequence.
    And will give up a particular code page after 20% of measured mess. Those criteria are customizable at will.

    The preemptive behavior DOES NOT replace the traditional detection workflow, it prioritize a particular code page
    but never take it for granted. Can improve the performance.

    You may want to focus your attention to some code page or/and not others, use cp_isolation and cp_exclusion for that
    purpose.

    This function will strip the SIG in the payload/sequence every time except on UTF-16, UTF-32.
    By default the library does not setup any handler other than the NullHandler, if you choose to set the 'explain'
    toggle to True it will alter the logger configuration to add a StreamHandler that is suitable for debugging.
    Custom logging format and handler can be set manually.
    \"\"\"

    if not isinstance(sequences, (bytearray, bytes)):
        raise TypeError(
            \"Expected object of type bytes or bytearray, got: {0}\".format(
                type(sequences)
            )
        )

    if explain:
        previous_logger_level: int = logger.level
        logger.addHandler(explain_handler)
        logger.setLevel(TRACE)

    length: int = len(sequences)

    if length == 0:
        logger.debug(\"Encoding detection on empty bytes, assuming utf_8 intention.\")
        if explain:
            logger.removeHandler(explain_handler)
            logger.setLevel(previous_logger_level or logging.WARNING)
        return CharsetMatches([CharsetMatch(sequences, \"utf_8\", 0.0, False, [], \"\")])

    if cp_isolation is not None:
        logger.log(
            TRACE,
            \"cp_isolation is set. use this flag for debugging purpose. \"
            \"limited list of encoding allowed : %s.\",
            \", \".join(cp_isolation),
        )
        cp_isolation = [iana_name(cp, False) for cp in cp_isolation]
    else:
        cp_isolation = []

    if cp_exclusion is not None:
        logger.log(
            TRACE,
            \"cp_exclusion is set. use this flag for debugging purpose. \"
            \"limited list of encoding excluded : %s.\",
            \", \".join(cp_exclusion),
        )
        cp_exclusion = [iana_name(cp, False) for cp in cp_exclusion]
    else:
        cp_exclusion = []

    if length <= (chunk_size * steps):
        logger.log(
            TRACE,
            \"override steps (%i) and chunk_size (%i) as content does not fit (%i byte(s) given) parameters.\",
            steps,
            chunk_size,
            length,
        )
        steps = 1
        chunk_size = length

    if steps > 1 and length / steps < chunk_size:
        chunk_size = int(length / steps)

    is_too_small_sequence: bool = len(sequences) < TOO_SMALL_SEQUENCE
    is_too_large_sequence: bool = len(sequences) >= TOO_BIG_SEQUENCE

    if is_too_small_sequence:
        logger.log(
            TRACE,
            \"Trying to detect encoding from a tiny portion of ({}) byte(s).\".format(
                length
            ),
        )
    elif is_too_large_sequence:
        logger.log(
            TRACE,
            \"Using lazy str decoding because the payload is quite large, ({}) byte(s).\".format(
                length
            ),
        )

    prioritized_encodings: List[str] = []

    specified_encoding: Optional[str] = (
        any_specified_encoding(sequences) if preemptive_behaviour else None
    )

    if specified_encoding is not None:
        prioritized_encodings.append(specified_encoding)
        logger.log(
            TRACE,
            \"Detected declarative mark in sequence. Priority +1 given for %s.\",
            specified_encoding,
        )

    tested: Set[str] = set()
    tested_but_hard_failure: List[str] = []
    tested_but_soft_failure: List[str] = []

    fallback_ascii: Optional[CharsetMatch] = None
    fallback_u8: Optional[CharsetMatch] = None
    fallback_specified: Optional[CharsetMatch] = None

    results: CharsetMatches = CharsetMatches()

    sig_encoding, sig_payload = identify_sig_or_bom(sequences)

    if sig_encoding is not None:
        prioritized_encodings.append(sig_encoding)
        logger.log(
            TRACE,
            \"Detected a SIG or BOM mark on first %i byte(s). Priority +1 given for %s.\",
            len(sig_payload),
            sig_encoding,
        )

    prioritized_encodings.append(\"ascii\")

    if \"utf_8\" not in prioritized_encodings:
        prioritized_encodings.append(\"utf_8\")

    for encoding_iana in prioritized_encodings + IANA_SUPPORTED:

        if cp_isolation and encoding_iana not in cp_isolation:
            continue

        if cp_exclusion and encoding_iana in cp_exclusion:
            continue

        if encoding_iana in tested:
            continue

        tested.add(encoding_iana)

        decoded_payload: Optional[str] = None
        bom_or_sig_available: bool = sig_encoding == encoding_iana
        strip_sig_or_bom: bool = bom_or_sig_available and should_strip_sig_or_bom(
            encoding_iana
        )

        if encoding_iana in {\"utf_16\", \"utf_32\"} and not bom_or_sig_available:
            logger.log(
                TRACE,
                \"Encoding %s wont be tested as-is because it require a BOM. Will try some sub-encoder LE/BE.\",
                encoding_iana,
            )
            continue

        try:
            is_multi_byte_decoder: bool = is_multi_byte_encoding(encoding_iana)
        except (ModuleNotFoundError, ImportError):
            logger.log(
                TRACE,
                \"Encoding %s does not provide an IncrementalDecoder\",
                encoding_iana,
            )
            continue

        try:
            if is_too_large_sequence and is_multi_byte_decoder is False:
                str(
                    sequences[: int(50e4)]
                    if strip_sig_or_bom is False
                    else sequences[len(sig_payload) : int(50e4)],
                    encoding=encoding_iana,
                )
            else:
                decoded_payload = str(
                    sequences
                    if strip_sig_or_bom is False
                    else sequences[len(sig_payload) :],
                    encoding=encoding_iana,
                )
        except (UnicodeDecodeError, LookupError) as e:
            if not isinstance(e, LookupError):
                logger.log(
                    TRACE,
                    \"Code page %s does not fit given bytes sequence at ALL. %s\",
                    encoding_iana,
                    str(e),
                )
            tested_but_hard_failure.append(encoding_iana)
            continue

        similar_soft_failure_test: bool = False

        for encoding_soft_failed in tested_but_soft_failure:
            if is_cp_similar(encoding_iana, encoding_soft_failed):
                similar_soft_failure_test = True
                break

        if similar_soft_failure_test:
            logger.log(
                TRACE,
                \"%s is deemed too similar to code page %s and was consider unsuited already. Continuing!\",
                encoding_iana,
                encoding_soft_failed,
            )
            continue

        r_ = range(
            0 if not bom_or_sig_available else len(sig_payload),
            length,
            int(length / steps),
        )

        multi_byte_bonus: bool = (
            is_multi_byte_decoder
            and decoded_payload is not None
            and len(decoded_payload) < length
        )

        if multi_byte_bonus:
            logger.log(
                TRACE,
                \"Code page %s is a multi byte encoding table and it appear that at least one character \"
                \"was encoded using n-bytes.\",
                encoding_iana,
            )

        max_chunk_gave_up: int = int(len(r_) / 4)

        max_chunk_gave_up = max(max_chunk_gave_up, 2)
        early_stop_count: int = 0
        lazy_str_hard_failure = False

        md_chunks: List[str] = []
        md_ratios = []

        try:
            for chunk in cut_sequence_chunks(
                sequences,
                encoding_iana,
                r_,
                chunk_size,
                bom_or_sig_available,
                strip_sig_or_bom,
                sig_payload,
                is_multi_byte_decoder,
                decoded_payload,
            ):
                md_chunks.append(chunk)

                md_ratios.append(mess_ratio(chunk, threshold))

                if md_ratios[-1] >= threshold:
                    early_stop_count += 1

                if (early_stop_count >= max_chunk_gave_up) or (
                    bom_or_sig_available and strip_sig_or_bom is False
                ):
                    break
        except UnicodeDecodeError as e:  # Lazy str loading may have missed something there
            logger.log(
                TRACE,
                \"LazyStr Loading: After MD chunk decode, code page %s does not fit given bytes sequence at ALL. %s\",
                encoding_iana,
                str(e),
            )
            early_stop_count = max_chunk_gave_up
            lazy_str_hard_failure = True

        # We might want to check the sequence again with the whole content
        # Only if initial MD tests passes
        if (
            not lazy_str_hard_failure
            and is_too_large_sequence
            and not is_multi_byte_decoder
        ):
            try:
                sequences[int(50e3) :].decode(encoding_iana, errors=\"strict\")
            except UnicodeDecodeError as e:
                logger.log(
                    TRACE,
                    \"LazyStr Loading: After final lookup, code page %s does not fit given bytes sequence at ALL. %s\",
                    encoding_iana,
                    str(e),
                )
                tested_but_hard_failure.append(encoding_iana)
                continue

        mean_mess_ratio: float = sum(md_ratios) / len(md_ratios) if md_ratios else 0.0
        if mean_mess_ratio >= threshold or early_stop_count >= max_chunk_gave_up:
            tested_but_soft_failure.append(encoding_iana)
            logger.log(
                TRACE,
                \"%s was excluded because of initial chaos probing. Gave up %i time(s). \"
                \"Computed mean chaos is %f %%.\",
                encoding_iana,
                early_stop_count,
                round(mean_mess_ratio * 100, ndigits=3),
            )
            # Preparing those fallbacks in case we got nothing.
            if (
                encoding_iana in [\"ascii\", \"utf_8\", specified_encoding]
                and not lazy_str_hard_failure
            ):
                fallback_entry = CharsetMatch(
                    sequences, encoding_iana, threshold, False, [], decoded_payload
                )
                if encoding_iana == specified_encoding:
                    fallback_specified = fallback_entry
                elif encoding_iana == \"ascii\":
                    fallback_ascii = fallback_entry
                else:
                    fallback_u8 = fallback_entry
            continue

        logger.log(
            TRACE,
            \"%s passed initial chaos probing. Mean measured chaos is %f %%\",
            encoding_iana,
            round(mean_mess_ratio * 100, ndigits=3),
        )

        if not is_multi_byte_decoder:
            target_languages: List[str] = encoding_languages(encoding_iana)
        else:
            target_languages = mb_encoding_languages(encoding_iana)

        if target_languages:
            logger.log(
                TRACE,
                \"{} should target any language(s) of {}\".format(
                    encoding_iana, str(target_languages)
                ),
            )

        cd_ratios = []

        # We shall skip the CD when its about ASCII
        # Most of the time its not relevant to run \"language-detection\" on it.
        if encoding_iana != \"ascii\":
            for chunk in md_chunks:
                chunk_languages = coherence_ratio(
                    chunk, 0.1, \",\".join(target_languages) if target_languages else None
                )

                cd_ratios.append(chunk_languages)

        cd_ratios_merged = merge_coherence_ratios(cd_ratios)

        if cd_ratios_merged:
            logger.log(
                TRACE,
                \"We detected language {} using {}\".format(
                    cd_ratios_merged, encoding_iana
                ),
            )

        results.append(
            CharsetMatch(
                sequences,
                encoding_iana,
                mean_mess_ratio,
                bom_or_sig_available,
                cd_ratios_merged,
                decoded_payload,
            )
        )

        if (
            encoding_iana in [specified_encoding, \"ascii\", \"utf_8\"]
            and mean_mess_ratio < 0.1
        ):
            logger.debug(
                \"Encoding detection: %s is most likely the one.\", encoding_iana
            )
            if explain:
                logger.removeHandler(explain_handler)
                logger.setLevel(previous_logger_level)
            return CharsetMatches([results[encoding_iana]])

        if encoding_iana == sig_encoding:
            logger.debug(
                \"Encoding detection: %s is most likely the one as we detected a BOM or SIG within \"
                \"the beginning of the sequence.\",
                encoding_iana,
            )
            if explain:
                logger.removeHandler(explain_handler)
                logger.setLevel(previous_logger_level)
            return CharsetMatches([results[encoding_iana]])

    if len(results) == 0:
        if fallback_u8 or fallback_ascii or fallback_specified:
            logger.log(
                TRACE,
                \"Nothing got out of the detection process. Using ASCII/UTF-8/Specified fallback.\",
            )

        if fallback_specified:
            logger.debug(
                \"Encoding detection: %s will be used as a fallback match\",
                fallback_specified.encoding,
            )
            results.append(fallback_specified)
        elif (
            (fallback_u8 and fallback_ascii is None)
            or (
                fallback_u8
                and fallback_ascii
                and fallback_u8.fingerprint != fallback_ascii.fingerprint
            )
            or (fallback_u8 is not None)
        ):
            logger.debug(\"Encoding detection: utf_8 will be used as a fallback match\")
            results.append(fallback_u8)
        elif fallback_ascii:
            logger.debug(\"Encoding detection: ascii will be used as a fallback match\")
            results.append(fallback_ascii)

    if results:
        logger.debug(
            \"Encoding detection: Found %s as plausible (best-candidate) for content. With %i alternatives.\",
            results.best().encoding,  # type: ignore
            len(results) - 1,
        )
    else:
        logger.debug(\"Encoding detection: Unable to determine any suitable charset.\")

    if explain:
        logger.removeHandler(explain_handler)
        logger.setLevel(previous_logger_level)

    return results


def from_fp(
    fp: BinaryIO,
    steps: int = 5,
    chunk_size: int = 512,
    threshold: float = 0.20,
    cp_isolation: List[str] = None,
    cp_exclusion: List[str] = None,
    preemptive_behaviour: bool = True,
    explain: bool = False,
) -> CharsetMatches:
    \"\"\"
    Same thing than the function from_bytes but using a file pointer that is already ready.
    Will not close the file pointer.
    \"\"\"
    return from_bytes(
        fp.read(),
        steps,
        chunk_size,
        threshold,
        cp_isolation,
        cp_exclusion,
        preemptive_behaviour,
        explain,
    )


def from_path(
    path: PathLike,
    steps: int = 5,
    chunk_size: int = 512,
    threshold: float = 0.20,
    cp_isolation: List[str] = None,
    cp_exclusion: List[str] = None,
    preemptive_behaviour: bool = True,
    explain: bool = False,
) -> CharsetMatches:
    \"\"\"
    Same thing than the function from_bytes but with one extra step. Opening and reading given file path in binary mode.
    Can raise IOError.
    \"\"\"
    with open(path, \"rb\") as fp:
        return from_fp(
            fp,
            steps,
            chunk_size,
            threshold,
            cp_isolation,
            cp_exclusion,
            preemptive_behaviour,
            explain,
        )


def normalize(
    path: PathLike,
    steps: int = 5,
    chunk_size: int = 512,
    threshold: float = 0.20,
    cp_isolation: List[str] = None,
    cp_exclusion: List[str] = None,
    preemptive_behaviour: bool = True,
) -> CharsetMatch:
    \"\"\"
    Take a (text-based) file path and try to create another file next to it, this time using UTF-8.
    \"\"\"
    results = from_path(
        path,
        steps,
        chunk_size,
        threshold,
        cp_isolation,
        cp_exclusion,
        preemptive_behaviour,
    )

    filename = basename(path)
    target_extensions = list(splitext(filename))

    if len(results) == 0:
        raise IOError(
            'Unable to normalize \"{}\", no encoding charset seems to fit.'.format(
                filename
            )
        )

    result = results.best()

    target_extensions[0] += \"-\" + result.encoding  # type: ignore

    with open(
        \"{}\".format(str(path).replace(filename, \"\".join(target_extensions))), \"wb\"
    ) as fp:
        fp.write(result.output())  # type: ignore

    return result  # type: ignore

"""
module_dict["charset_normalizer"+os.sep+"version.py"]="""
\"\"\"
Expose version
\"\"\"

__version__ = \"2.1.0\"
VERSION = __version__.split(\".\")

"""
module_dict["charset_normalizer"+os.sep+"md.py"]="""
from functools import lru_cache
from typing import List, Optional

from .constant import COMMON_SAFE_ASCII_CHARACTERS, UNICODE_SECONDARY_RANGE_KEYWORD
from .utils import (
    is_accentuated,
    is_ascii,
    is_case_variable,
    is_cjk,
    is_emoticon,
    is_hangul,
    is_hiragana,
    is_katakana,
    is_latin,
    is_punctuation,
    is_separator,
    is_symbol,
    is_thai,
    is_unprintable,
    remove_accent,
    unicode_range,
)


class MessDetectorPlugin:
    \"\"\"
    Base abstract class used for mess detection plugins.
    All detectors MUST extend and implement given methods.
    \"\"\"

    def eligible(self, character: str) -> bool:
        \"\"\"
        Determine if given character should be fed in.
        \"\"\"
        raise NotImplementedError  # pragma: nocover

    def feed(self, character: str) -> None:
        \"\"\"
        The main routine to be executed upon character.
        Insert the logic in witch the text would be considered chaotic.
        \"\"\"
        raise NotImplementedError  # pragma: nocover

    def reset(self) -> None:  # pragma: no cover
        \"\"\"
        Permit to reset the plugin to the initial state.
        \"\"\"
        raise NotImplementedError

    @property
    def ratio(self) -> float:
        \"\"\"
        Compute the chaos ratio based on what your feed() has seen.
        Must NOT be lower than 0.; No restriction gt 0.
        \"\"\"
        raise NotImplementedError  # pragma: nocover


class TooManySymbolOrPunctuationPlugin(MessDetectorPlugin):
    def __init__(self) -> None:
        self._punctuation_count: int = 0
        self._symbol_count: int = 0
        self._character_count: int = 0

        self._last_printable_char: Optional[str] = None
        self._frenzy_symbol_in_word: bool = False

    def eligible(self, character: str) -> bool:
        return character.isprintable()

    def feed(self, character: str) -> None:
        self._character_count += 1

        if (
            character != self._last_printable_char
            and character not in COMMON_SAFE_ASCII_CHARACTERS
        ):
            if is_punctuation(character):
                self._punctuation_count += 1
            elif (
                character.isdigit() is False
                and is_symbol(character)
                and is_emoticon(character) is False
            ):
                self._symbol_count += 2

        self._last_printable_char = character

    def reset(self) -> None:  # pragma: no cover
        self._punctuation_count = 0
        self._character_count = 0
        self._symbol_count = 0

    @property
    def ratio(self) -> float:
        if self._character_count == 0:
            return 0.0

        ratio_of_punctuation: float = (
            self._punctuation_count + self._symbol_count
        ) / self._character_count

        return ratio_of_punctuation if ratio_of_punctuation >= 0.3 else 0.0


class TooManyAccentuatedPlugin(MessDetectorPlugin):
    def __init__(self) -> None:
        self._character_count: int = 0
        self._accentuated_count: int = 0

    def eligible(self, character: str) -> bool:
        return character.isalpha()

    def feed(self, character: str) -> None:
        self._character_count += 1

        if is_accentuated(character):
            self._accentuated_count += 1

    def reset(self) -> None:  # pragma: no cover
        self._character_count = 0
        self._accentuated_count = 0

    @property
    def ratio(self) -> float:
        if self._character_count == 0:
            return 0.0
        ratio_of_accentuation: float = self._accentuated_count / self._character_count
        return ratio_of_accentuation if ratio_of_accentuation >= 0.35 else 0.0


class UnprintablePlugin(MessDetectorPlugin):
    def __init__(self) -> None:
        self._unprintable_count: int = 0
        self._character_count: int = 0

    def eligible(self, character: str) -> bool:
        return True

    def feed(self, character: str) -> None:
        if is_unprintable(character):
            self._unprintable_count += 1
        self._character_count += 1

    def reset(self) -> None:  # pragma: no cover
        self._unprintable_count = 0

    @property
    def ratio(self) -> float:
        if self._character_count == 0:
            return 0.0

        return (self._unprintable_count * 8) / self._character_count


class SuspiciousDuplicateAccentPlugin(MessDetectorPlugin):
    def __init__(self) -> None:
        self._successive_count: int = 0
        self._character_count: int = 0

        self._last_latin_character: Optional[str] = None

    def eligible(self, character: str) -> bool:
        return character.isalpha() and is_latin(character)

    def feed(self, character: str) -> None:
        self._character_count += 1
        if (
            self._last_latin_character is not None
            and is_accentuated(character)
            and is_accentuated(self._last_latin_character)
        ):
            if character.isupper() and self._last_latin_character.isupper():
                self._successive_count += 1
            # Worse if its the same char duplicated with different accent.
            if remove_accent(character) == remove_accent(self._last_latin_character):
                self._successive_count += 1
        self._last_latin_character = character

    def reset(self) -> None:  # pragma: no cover
        self._successive_count = 0
        self._character_count = 0
        self._last_latin_character = None

    @property
    def ratio(self) -> float:
        if self._character_count == 0:
            return 0.0

        return (self._successive_count * 2) / self._character_count


class SuspiciousRange(MessDetectorPlugin):
    def __init__(self) -> None:
        self._suspicious_successive_range_count: int = 0
        self._character_count: int = 0
        self._last_printable_seen: Optional[str] = None

    def eligible(self, character: str) -> bool:
        return character.isprintable()

    def feed(self, character: str) -> None:
        self._character_count += 1

        if (
            character.isspace()
            or is_punctuation(character)
            or character in COMMON_SAFE_ASCII_CHARACTERS
        ):
            self._last_printable_seen = None
            return

        if self._last_printable_seen is None:
            self._last_printable_seen = character
            return

        unicode_range_a: Optional[str] = unicode_range(self._last_printable_seen)
        unicode_range_b: Optional[str] = unicode_range(character)

        if is_suspiciously_successive_range(unicode_range_a, unicode_range_b):
            self._suspicious_successive_range_count += 1

        self._last_printable_seen = character

    def reset(self) -> None:  # pragma: no cover
        self._character_count = 0
        self._suspicious_successive_range_count = 0
        self._last_printable_seen = None

    @property
    def ratio(self) -> float:
        if self._character_count == 0:
            return 0.0

        ratio_of_suspicious_range_usage: float = (
            self._suspicious_successive_range_count * 2
        ) / self._character_count

        if ratio_of_suspicious_range_usage < 0.1:
            return 0.0

        return ratio_of_suspicious_range_usage


class SuperWeirdWordPlugin(MessDetectorPlugin):
    def __init__(self) -> None:
        self._word_count: int = 0
        self._bad_word_count: int = 0
        self._foreign_long_count: int = 0

        self._is_current_word_bad: bool = False
        self._foreign_long_watch: bool = False

        self._character_count: int = 0
        self._bad_character_count: int = 0

        self._buffer: str = \"\"
        self._buffer_accent_count: int = 0

    def eligible(self, character: str) -> bool:
        return True

    def feed(self, character: str) -> None:
        if character.isalpha():
            self._buffer += character
            if is_accentuated(character):
                self._buffer_accent_count += 1
            if (
                self._foreign_long_watch is False
                and (is_latin(character) is False or is_accentuated(character))
                and is_cjk(character) is False
                and is_hangul(character) is False
                and is_katakana(character) is False
                and is_hiragana(character) is False
                and is_thai(character) is False
            ):
                self._foreign_long_watch = True
            return
        if not self._buffer:
            return
        if (
            character.isspace() or is_punctuation(character) or is_separator(character)
        ) and self._buffer:
            self._word_count += 1
            buffer_length: int = len(self._buffer)

            self._character_count += buffer_length

            if buffer_length >= 4:
                if self._buffer_accent_count / buffer_length > 0.34:
                    self._is_current_word_bad = True
                # Word/Buffer ending with a upper case accentuated letter are so rare,
                # that we will consider them all as suspicious. Same weight as foreign_long suspicious.
                if is_accentuated(self._buffer[-1]) and self._buffer[-1].isupper():
                    self._foreign_long_count += 1
                    self._is_current_word_bad = True
            if buffer_length >= 24 and self._foreign_long_watch:
                self._foreign_long_count += 1
                self._is_current_word_bad = True

            if self._is_current_word_bad:
                self._bad_word_count += 1
                self._bad_character_count += len(self._buffer)
                self._is_current_word_bad = False

            self._foreign_long_watch = False
            self._buffer = \"\"
            self._buffer_accent_count = 0
        elif (
            character not in {\"<\", \">\", \"-\", \"=\", \"~\", \"|\", \"_\"}
            and character.isdigit() is False
            and is_symbol(character)
        ):
            self._is_current_word_bad = True
            self._buffer += character

    def reset(self) -> None:  # pragma: no cover
        self._buffer = \"\"
        self._is_current_word_bad = False
        self._foreign_long_watch = False
        self._bad_word_count = 0
        self._word_count = 0
        self._character_count = 0
        self._bad_character_count = 0
        self._foreign_long_count = 0

    @property
    def ratio(self) -> float:
        if self._word_count <= 10 and self._foreign_long_count == 0:
            return 0.0

        return self._bad_character_count / self._character_count


class CjkInvalidStopPlugin(MessDetectorPlugin):
    \"\"\"
    GB(Chinese) based encoding often render the stop incorrectly when the content does not fit and
    can be easily detected. Searching for the overuse of '丅' and '丄'.
    \"\"\"

    def __init__(self) -> None:
        self._wrong_stop_count: int = 0
        self._cjk_character_count: int = 0

    def eligible(self, character: str) -> bool:
        return True

    def feed(self, character: str) -> None:
        if character in {\"丅\", \"丄\"}:
            self._wrong_stop_count += 1
            return
        if is_cjk(character):
            self._cjk_character_count += 1

    def reset(self) -> None:  # pragma: no cover
        self._wrong_stop_count = 0
        self._cjk_character_count = 0

    @property
    def ratio(self) -> float:
        if self._cjk_character_count < 16:
            return 0.0
        return self._wrong_stop_count / self._cjk_character_count


class ArchaicUpperLowerPlugin(MessDetectorPlugin):
    def __init__(self) -> None:
        self._buf: bool = False

        self._character_count_since_last_sep: int = 0

        self._successive_upper_lower_count: int = 0
        self._successive_upper_lower_count_final: int = 0

        self._character_count: int = 0

        self._last_alpha_seen: Optional[str] = None
        self._current_ascii_only: bool = True

    def eligible(self, character: str) -> bool:
        return True

    def feed(self, character: str) -> None:
        is_concerned = character.isalpha() and is_case_variable(character)
        chunk_sep = is_concerned is False

        if chunk_sep and self._character_count_since_last_sep > 0:
            if (
                self._character_count_since_last_sep <= 64
                and character.isdigit() is False
                and self._current_ascii_only is False
            ):
                self._successive_upper_lower_count_final += (
                    self._successive_upper_lower_count
                )

            self._successive_upper_lower_count = 0
            self._character_count_since_last_sep = 0
            self._last_alpha_seen = None
            self._buf = False
            self._character_count += 1
            self._current_ascii_only = True

            return

        if self._current_ascii_only is True and is_ascii(character) is False:
            self._current_ascii_only = False

        if self._last_alpha_seen is not None:
            if (character.isupper() and self._last_alpha_seen.islower()) or (
                character.islower() and self._last_alpha_seen.isupper()
            ):
                if self._buf is True:
                    self._successive_upper_lower_count += 2
                    self._buf = False
                else:
                    self._buf = True
            else:
                self._buf = False

        self._character_count += 1
        self._character_count_since_last_sep += 1
        self._last_alpha_seen = character

    def reset(self) -> None:  # pragma: no cover
        self._character_count = 0
        self._character_count_since_last_sep = 0
        self._successive_upper_lower_count = 0
        self._successive_upper_lower_count_final = 0
        self._last_alpha_seen = None
        self._buf = False
        self._current_ascii_only = True

    @property
    def ratio(self) -> float:
        if self._character_count == 0:
            return 0.0

        return self._successive_upper_lower_count_final / self._character_count


@lru_cache(maxsize=1024)
def is_suspiciously_successive_range(
    unicode_range_a: Optional[str], unicode_range_b: Optional[str]
) -> bool:
    \"\"\"
    Determine if two Unicode range seen next to each other can be considered as suspicious.
    \"\"\"
    if unicode_range_a is None or unicode_range_b is None:
        return True

    if unicode_range_a == unicode_range_b:
        return False

    if \"Latin\" in unicode_range_a and \"Latin\" in unicode_range_b:
        return False

    if \"Emoticons\" in unicode_range_a or \"Emoticons\" in unicode_range_b:
        return False

    # Latin characters can be accompanied with a combining diacritical mark
    # eg. Vietnamese.
    if (\"Latin\" in unicode_range_a or \"Latin\" in unicode_range_b) and (
        \"Combining\" in unicode_range_a or \"Combining\" in unicode_range_b
    ):
        return False

    keywords_range_a, keywords_range_b = unicode_range_a.split(
        \" \"
    ), unicode_range_b.split(\" \")

    for el in keywords_range_a:
        if el in UNICODE_SECONDARY_RANGE_KEYWORD:
            continue
        if el in keywords_range_b:
            return False

    # Japanese Exception
    range_a_jp_chars, range_b_jp_chars = (
        unicode_range_a
        in (
            \"Hiragana\",
            \"Katakana\",
        ),
        unicode_range_b in (\"Hiragana\", \"Katakana\"),
    )
    if (range_a_jp_chars or range_b_jp_chars) and (
        \"CJK\" in unicode_range_a or \"CJK\" in unicode_range_b
    ):
        return False
    if range_a_jp_chars and range_b_jp_chars:
        return False

    if \"Hangul\" in unicode_range_a or \"Hangul\" in unicode_range_b:
        if \"CJK\" in unicode_range_a or \"CJK\" in unicode_range_b:
            return False
        if unicode_range_a == \"Basic Latin\" or unicode_range_b == \"Basic Latin\":
            return False

    # Chinese/Japanese use dedicated range for punctuation and/or separators.
    if (\"CJK\" in unicode_range_a or \"CJK\" in unicode_range_b) or (
        unicode_range_a in [\"Katakana\", \"Hiragana\"]
        and unicode_range_b in [\"Katakana\", \"Hiragana\"]
    ):
        if \"Punctuation\" in unicode_range_a or \"Punctuation\" in unicode_range_b:
            return False
        if \"Forms\" in unicode_range_a or \"Forms\" in unicode_range_b:
            return False

    return True


@lru_cache(maxsize=2048)
def mess_ratio(
    decoded_sequence: str, maximum_threshold: float = 0.2, debug: bool = False
) -> float:
    \"\"\"
    Compute a mess ratio given a decoded bytes sequence. The maximum threshold does stop the computation earlier.
    \"\"\"

    detectors: List[MessDetectorPlugin] = [
        md_class() for md_class in MessDetectorPlugin.__subclasses__()
    ]

    length: int = len(decoded_sequence) + 1

    mean_mess_ratio: float = 0.0

    if length < 512:
        intermediary_mean_mess_ratio_calc: int = 32
    elif length <= 1024:
        intermediary_mean_mess_ratio_calc = 64
    else:
        intermediary_mean_mess_ratio_calc = 128

    for character, index in zip(decoded_sequence + \"\\n\", range(length)):
        for detector in detectors:
            if detector.eligible(character):
                detector.feed(character)

        if (
            index > 0 and index % intermediary_mean_mess_ratio_calc == 0
        ) or index == length - 1:
            mean_mess_ratio = sum(dt.ratio for dt in detectors)

            if mean_mess_ratio >= maximum_threshold:
                break

    if debug:
        for dt in detectors:  # pragma: nocover
            print(dt.__class__, dt.ratio)

    return round(mean_mess_ratio, 3)

"""
module_dict["charset_normalizer"+os.sep+"__init__.py"]="""
# -*- coding: utf_8 -*-
\"\"\"
Charset-Normalizer
~~~~~~~~~~~~~~
The Real First Universal Charset Detector.
A library that helps you read text from an unknown charset encoding.
Motivated by chardet, This package is trying to resolve the issue by taking a new approach.
All IANA character set names for which the Python core library provides codecs are supported.

Basic usage:
   >>> from charset_normalizer import from_bytes
   >>> results = from_bytes('Bсеки човек има право на образование. Oбразованието!'.encode('utf_8'))
   >>> best_guess = results.best()
   >>> str(best_guess)
   'Bсеки човек има право на образование. Oбразованието!'

Others methods and usages are available - see the full documentation
at <https://github.com/Ousret/charset_normalizer>.
:copyright: (c) 2021 by Ahmed TAHRI
:license: MIT, see LICENSE for more details.
\"\"\"
import logging

from .api import from_bytes, from_fp, from_path, normalize
from .legacy import (
    CharsetDetector,
    CharsetDoctor,
    CharsetNormalizerMatch,
    CharsetNormalizerMatches,
    detect,
)
from .models import CharsetMatch, CharsetMatches
from .utils import set_logging_handler
from .version import VERSION, __version__

__all__ = (
    \"from_fp\",
    \"from_path\",
    \"from_bytes\",
    \"normalize\",
    \"detect\",
    \"CharsetMatch\",
    \"CharsetMatches\",
    \"CharsetNormalizerMatch\",
    \"CharsetNormalizerMatches\",
    \"CharsetDetector\",
    \"CharsetDoctor\",
    \"__version__\",
    \"VERSION\",
    \"set_logging_handler\",
)

# Attach a NullHandler to the top level logger by default
# https://docs.python.org/3.3/howto/logging.html#configuring-logging-for-a-library

logging.getLogger(\"charset_normalizer\").addHandler(logging.NullHandler())

"""
module_dict["charset_normalizer"+os.sep+"utils.py"]="""
# < include 'unicodedata2.py' >

try:
    # WARNING: unicodedata2 support is going to be removed in 3.0
    # Python is quickly catching up.
    import unicodedata2 as unicodedata
except ImportError:
    import unicodedata  # type: ignore[no-redef]

import importlib
import logging
from codecs import IncrementalDecoder
from encodings.aliases import aliases
from functools import lru_cache
from re import findall
from typing import Generator, List, Optional, Set, Tuple, Union

from _multibytecodec import MultibyteIncrementalDecoder  # type: ignore

from .constant import (
    ENCODING_MARKS,
    IANA_SUPPORTED_SIMILAR,
    RE_POSSIBLE_ENCODING_INDICATION,
    UNICODE_RANGES_COMBINED,
    UNICODE_SECONDARY_RANGE_KEYWORD,
    UTF8_MAXIMAL_ALLOCATION,
)


@lru_cache(maxsize=UTF8_MAXIMAL_ALLOCATION)
def is_accentuated(character: str) -> bool:
    try:
        description: str = unicodedata.name(character)
    except ValueError:
        return False
    return (
        \"WITH GRAVE\" in description
        or \"WITH ACUTE\" in description
        or \"WITH CEDILLA\" in description
        or \"WITH DIAERESIS\" in description
        or \"WITH CIRCUMFLEX\" in description
        or \"WITH TILDE\" in description
    )


@lru_cache(maxsize=UTF8_MAXIMAL_ALLOCATION)
def remove_accent(character: str) -> str:
    decomposed: str = unicodedata.decomposition(character)
    if not decomposed:
        return character

    codes: List[str] = decomposed.split(\" \")

    return chr(int(codes[0], 16))


@lru_cache(maxsize=UTF8_MAXIMAL_ALLOCATION)
def unicode_range(character: str) -> Optional[str]:
    \"\"\"
    Retrieve the Unicode range official name from a single character.
    \"\"\"
    character_ord: int = ord(character)

    for range_name, ord_range in UNICODE_RANGES_COMBINED.items():
        if character_ord in ord_range:
            return range_name

    return None


@lru_cache(maxsize=UTF8_MAXIMAL_ALLOCATION)
def is_latin(character: str) -> bool:
    try:
        description: str = unicodedata.name(character)
    except ValueError:
        return False
    return \"LATIN\" in description


@lru_cache(maxsize=UTF8_MAXIMAL_ALLOCATION)
def is_ascii(character: str) -> bool:
    try:
        character.encode(\"ascii\")
    except UnicodeEncodeError:
        return False
    return True


@lru_cache(maxsize=UTF8_MAXIMAL_ALLOCATION)
def is_punctuation(character: str) -> bool:
    character_category: str = unicodedata.category(character)

    if \"P\" in character_category:
        return True

    character_range: Optional[str] = unicode_range(character)

    if character_range is None:
        return False

    return \"Punctuation\" in character_range


@lru_cache(maxsize=UTF8_MAXIMAL_ALLOCATION)
def is_symbol(character: str) -> bool:
    character_category: str = unicodedata.category(character)

    if \"S\" in character_category or \"N\" in character_category:
        return True

    character_range: Optional[str] = unicode_range(character)

    if character_range is None:
        return False

    return \"Forms\" in character_range


@lru_cache(maxsize=UTF8_MAXIMAL_ALLOCATION)
def is_emoticon(character: str) -> bool:
    character_range: Optional[str] = unicode_range(character)

    if character_range is None:
        return False

    return \"Emoticons\" in character_range


@lru_cache(maxsize=UTF8_MAXIMAL_ALLOCATION)
def is_separator(character: str) -> bool:
    if character.isspace() or character in {\"｜\", \"+\", \",\", \";\", \"<\", \">\"}:
        return True

    character_category: str = unicodedata.category(character)

    return \"Z\" in character_category


@lru_cache(maxsize=UTF8_MAXIMAL_ALLOCATION)
def is_case_variable(character: str) -> bool:
    return character.islower() != character.isupper()


def is_private_use_only(character: str) -> bool:
    character_category: str = unicodedata.category(character)

    return character_category == \"Co\"


@lru_cache(maxsize=UTF8_MAXIMAL_ALLOCATION)
def is_cjk(character: str) -> bool:
    try:
        character_name = unicodedata.name(character)
    except ValueError:
        return False

    return \"CJK\" in character_name


@lru_cache(maxsize=UTF8_MAXIMAL_ALLOCATION)
def is_hiragana(character: str) -> bool:
    try:
        character_name = unicodedata.name(character)
    except ValueError:
        return False

    return \"HIRAGANA\" in character_name


@lru_cache(maxsize=UTF8_MAXIMAL_ALLOCATION)
def is_katakana(character: str) -> bool:
    try:
        character_name = unicodedata.name(character)
    except ValueError:
        return False

    return \"KATAKANA\" in character_name


@lru_cache(maxsize=UTF8_MAXIMAL_ALLOCATION)
def is_hangul(character: str) -> bool:
    try:
        character_name = unicodedata.name(character)
    except ValueError:
        return False

    return \"HANGUL\" in character_name


@lru_cache(maxsize=UTF8_MAXIMAL_ALLOCATION)
def is_thai(character: str) -> bool:
    try:
        character_name = unicodedata.name(character)
    except ValueError:
        return False

    return \"THAI\" in character_name


@lru_cache(maxsize=len(UNICODE_RANGES_COMBINED))
def is_unicode_range_secondary(range_name: str) -> bool:
    return any(keyword in range_name for keyword in UNICODE_SECONDARY_RANGE_KEYWORD)


@lru_cache(maxsize=UTF8_MAXIMAL_ALLOCATION)
def is_unprintable(character: str) -> bool:
    return (
        character.isspace() is False  # includes \\n \\t \\r \\v
        and character.isprintable() is False
        and character != \"\\x1A\"  # Why? Its the ASCII substitute character.
        and character != b\"\\xEF\\xBB\\xBF\".decode(\"utf_8\")  # bug discovered in Python,
        # Zero Width No-Break Space located in 	Arabic Presentation Forms-B, Unicode 1.1 not acknowledged as space.
    )


def any_specified_encoding(sequence: bytes, search_zone: int = 4096) -> Optional[str]:
    \"\"\"
    Extract using ASCII-only decoder any specified encoding in the first n-bytes.
    \"\"\"
    if not isinstance(sequence, bytes):
        raise TypeError

    seq_len: int = len(sequence)

    results: List[str] = findall(
        RE_POSSIBLE_ENCODING_INDICATION,
        sequence[: min(seq_len, search_zone)].decode(\"ascii\", errors=\"ignore\"),
    )

    if len(results) == 0:
        return None

    for specified_encoding in results:
        specified_encoding = specified_encoding.lower().replace(\"-\", \"_\")

        for encoding_alias, encoding_iana in aliases.items():
            if encoding_alias == specified_encoding:
                return encoding_iana
            if encoding_iana == specified_encoding:
                return encoding_iana

    return None


@lru_cache(maxsize=128)
def is_multi_byte_encoding(name: str) -> bool:
    \"\"\"
    Verify is a specific encoding is a multi byte one based on it IANA name
    \"\"\"
    return name in {
        \"utf_8\",
        \"utf_8_sig\",
        \"utf_16\",
        \"utf_16_be\",
        \"utf_16_le\",
        \"utf_32\",
        \"utf_32_le\",
        \"utf_32_be\",
        \"utf_7\",
    } or issubclass(
        importlib.import_module(\"encodings.{}\".format(name)).IncrementalDecoder,  # type: ignore
        MultibyteIncrementalDecoder,
    )


def identify_sig_or_bom(sequence: bytes) -> Tuple[Optional[str], bytes]:
    \"\"\"
    Identify and extract SIG/BOM in given sequence.
    \"\"\"

    for iana_encoding in ENCODING_MARKS:
        marks: Union[bytes, List[bytes]] = ENCODING_MARKS[iana_encoding]

        if isinstance(marks, bytes):
            marks = [marks]

        for mark in marks:
            if sequence.startswith(mark):
                return iana_encoding, mark

    return None, b\"\"


def should_strip_sig_or_bom(iana_encoding: str) -> bool:
    return iana_encoding not in {\"utf_16\", \"utf_32\"}


def iana_name(cp_name: str, strict: bool = True) -> str:
    cp_name = cp_name.lower().replace(\"-\", \"_\")

    for encoding_alias, encoding_iana in aliases.items():
        if cp_name in [encoding_alias, encoding_iana]:
            return encoding_iana

    if strict:
        raise ValueError(\"Unable to retrieve IANA for '{}'\".format(cp_name))

    return cp_name


def range_scan(decoded_sequence: str) -> List[str]:
    ranges: Set[str] = set()

    for character in decoded_sequence:
        character_range: Optional[str] = unicode_range(character)

        if character_range is None:
            continue

        ranges.add(character_range)

    return list(ranges)


def cp_similarity(iana_name_a: str, iana_name_b: str) -> float:

    if is_multi_byte_encoding(iana_name_a) or is_multi_byte_encoding(iana_name_b):
        return 0.0

    decoder_a = importlib.import_module(\"encodings.{}\".format(iana_name_a)).IncrementalDecoder  # type: ignore
    decoder_b = importlib.import_module(\"encodings.{}\".format(iana_name_b)).IncrementalDecoder  # type: ignore

    id_a: IncrementalDecoder = decoder_a(errors=\"ignore\")
    id_b: IncrementalDecoder = decoder_b(errors=\"ignore\")

    character_match_count: int = 0

    for i in range(255):
        to_be_decoded: bytes = bytes([i])
        if id_a.decode(to_be_decoded) == id_b.decode(to_be_decoded):
            character_match_count += 1

    return character_match_count / 254


def is_cp_similar(iana_name_a: str, iana_name_b: str) -> bool:
    \"\"\"
    Determine if two code page are at least 80% similar. IANA_SUPPORTED_SIMILAR dict was generated using
    the function cp_similarity.
    \"\"\"
    return (
        iana_name_a in IANA_SUPPORTED_SIMILAR
        and iana_name_b in IANA_SUPPORTED_SIMILAR[iana_name_a]
    )


def set_logging_handler(
    name: str = \"charset_normalizer\",
    level: int = logging.INFO,
    format_string: str = \"%(asctime)s | %(levelname)s | %(message)s\",
) -> None:

    logger = logging.getLogger(name)
    logger.setLevel(level)

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(format_string))
    logger.addHandler(handler)


def cut_sequence_chunks(
    sequences: bytes,
    encoding_iana: str,
    offsets: range,
    chunk_size: int,
    bom_or_sig_available: bool,
    strip_sig_or_bom: bool,
    sig_payload: bytes,
    is_multi_byte_decoder: bool,
    decoded_payload: Optional[str] = None,
) -> Generator[str, None, None]:

    if decoded_payload and is_multi_byte_decoder is False:
        for i in offsets:
            chunk = decoded_payload[i : i + chunk_size]
            if not chunk:
                break
            yield chunk
    else:
        for i in offsets:
            chunk_end = i + chunk_size
            if chunk_end > len(sequences) + 8:
                continue

            cut_sequence = sequences[i : i + chunk_size]

            if bom_or_sig_available and strip_sig_or_bom is False:
                cut_sequence = sig_payload + cut_sequence

            chunk = cut_sequence.decode(
                encoding_iana,
                errors=\"ignore\" if is_multi_byte_decoder else \"strict\",
            )

            # multi-byte bad cutting detector and adjustment
            # not the cleanest way to perform that fix but clever enough for now.
            if is_multi_byte_decoder and i > 0 and sequences[i] >= 0x80:

                chunk_partial_size_chk: int = min(chunk_size, 16)

                if (
                    decoded_payload
                    and chunk[:chunk_partial_size_chk] not in decoded_payload
                ):
                    for j in range(i, i - 4, -1):
                        cut_sequence = sequences[j:chunk_end]

                        if bom_or_sig_available and strip_sig_or_bom is False:
                            cut_sequence = sig_payload + cut_sequence

                        chunk = cut_sequence.decode(encoding_iana, errors=\"ignore\")

                        if chunk[:chunk_partial_size_chk] in decoded_payload:
                            break

            yield chunk

"""
module_dict["charset_normalizer"+os.sep+"cd.py"]="""
import importlib
from codecs import IncrementalDecoder
from collections import Counter
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

from .assets import FREQUENCIES
from .constant import KO_NAMES, LANGUAGE_SUPPORTED_COUNT, TOO_SMALL_SEQUENCE, ZH_NAMES
from .md import is_suspiciously_successive_range
from .models import CoherenceMatches
from .utils import (
    is_accentuated,
    is_latin,
    is_multi_byte_encoding,
    is_unicode_range_secondary,
    unicode_range,
)


def encoding_unicode_range(iana_name: str) -> List[str]:
    \"\"\"
    Return associated unicode ranges in a single byte code page.
    \"\"\"
    if is_multi_byte_encoding(iana_name):
        raise IOError(\"Function not supported on multi-byte code page\")

    decoder = importlib.import_module(\"encodings.{}\".format(iana_name)).IncrementalDecoder  # type: ignore

    p: IncrementalDecoder = decoder(errors=\"ignore\")
    seen_ranges: Dict[str, int] = {}
    character_count: int = 0

    for i in range(0x40, 0xFF):
        chunk: str = p.decode(bytes([i]))

        if chunk:
            character_range: Optional[str] = unicode_range(chunk)

            if character_range is None:
                continue

            if is_unicode_range_secondary(character_range) is False:
                if character_range not in seen_ranges:
                    seen_ranges[character_range] = 0
                seen_ranges[character_range] += 1
                character_count += 1

    return sorted(
        [
            character_range
            for character_range in seen_ranges
            if seen_ranges[character_range] / character_count >= 0.15
        ]
    )


def unicode_range_languages(primary_range: str) -> List[str]:
    \"\"\"
    Return inferred languages used with a unicode range.
    \"\"\"
    languages: List[str] = []

    for language, characters in FREQUENCIES.items():
        for character in characters:
            if unicode_range(character) == primary_range:
                languages.append(language)
                break

    return languages


@lru_cache()
def encoding_languages(iana_name: str) -> List[str]:
    \"\"\"
    Single-byte encoding language association. Some code page are heavily linked to particular language(s).
    This function does the correspondence.
    \"\"\"
    unicode_ranges: List[str] = encoding_unicode_range(iana_name)
    primary_range: Optional[str] = None

    for specified_range in unicode_ranges:
        if \"Latin\" not in specified_range:
            primary_range = specified_range
            break

    if primary_range is None:
        return [\"Latin Based\"]

    return unicode_range_languages(primary_range)


@lru_cache()
def mb_encoding_languages(iana_name: str) -> List[str]:
    \"\"\"
    Multi-byte encoding language association. Some code page are heavily linked to particular language(s).
    This function does the correspondence.
    \"\"\"
    if (
        iana_name.startswith(\"shift_\")
        or iana_name.startswith(\"iso2022_jp\")
        or iana_name.startswith(\"euc_j\")
        or iana_name == \"cp932\"
    ):
        return [\"Japanese\"]
    if iana_name.startswith(\"gb\") or iana_name in ZH_NAMES:
        return [\"Chinese\", \"Classical Chinese\"]
    if iana_name.startswith(\"iso2022_kr\") or iana_name in KO_NAMES:
        return [\"Korean\"]

    return []


@lru_cache(maxsize=LANGUAGE_SUPPORTED_COUNT)
def get_target_features(language: str) -> Tuple[bool, bool]:
    \"\"\"
    Determine main aspects from a supported language if it contains accents and if is pure Latin.
    \"\"\"
    target_have_accents: bool = False
    target_pure_latin: bool = True

    for character in FREQUENCIES[language]:
        if not target_have_accents and is_accentuated(character):
            target_have_accents = True
        if target_pure_latin and is_latin(character) is False:
            target_pure_latin = False

    return target_have_accents, target_pure_latin


def alphabet_languages(
    characters: List[str], ignore_non_latin: bool = False
) -> List[str]:
    \"\"\"
    Return associated languages associated to given characters.
    \"\"\"
    languages: List[Tuple[str, float]] = []

    source_have_accents = any(is_accentuated(character) for character in characters)

    for language, language_characters in FREQUENCIES.items():

        target_have_accents, target_pure_latin = get_target_features(language)

        if ignore_non_latin and target_pure_latin is False:
            continue

        if target_have_accents is False and source_have_accents:
            continue

        character_count: int = len(language_characters)

        character_match_count: int = len(
            [c for c in language_characters if c in characters]
        )

        ratio: float = character_match_count / character_count

        if ratio >= 0.2:
            languages.append((language, ratio))

    languages = sorted(languages, key=lambda x: x[1], reverse=True)

    return [compatible_language[0] for compatible_language in languages]


def characters_popularity_compare(
    language: str, ordered_characters: List[str]
) -> float:
    \"\"\"
    Determine if a ordered characters list (by occurrence from most appearance to rarest) match a particular language.
    The result is a ratio between 0. (absolutely no correspondence) and 1. (near perfect fit).
    Beware that is function is not strict on the match in order to ease the detection. (Meaning close match is 1.)
    \"\"\"
    if language not in FREQUENCIES:
        raise ValueError(\"{} not available\".format(language))

    character_approved_count: int = 0
    FREQUENCIES_language_set = set(FREQUENCIES[language])

    for character in ordered_characters:
        if character not in FREQUENCIES_language_set:
            continue

        characters_before_source: List[str] = FREQUENCIES[language][
            0 : FREQUENCIES[language].index(character)
        ]
        characters_after_source: List[str] = FREQUENCIES[language][
            FREQUENCIES[language].index(character) :
        ]
        characters_before: List[str] = ordered_characters[
            0 : ordered_characters.index(character)
        ]
        characters_after: List[str] = ordered_characters[
            ordered_characters.index(character) :
        ]

        before_match_count: int = len(
            set(characters_before) & set(characters_before_source)
        )

        after_match_count: int = len(
            set(characters_after) & set(characters_after_source)
        )

        if len(characters_before_source) == 0 and before_match_count <= 4:
            character_approved_count += 1
            continue

        if len(characters_after_source) == 0 and after_match_count <= 4:
            character_approved_count += 1
            continue

        if (
            before_match_count / len(characters_before_source) >= 0.4
            or after_match_count / len(characters_after_source) >= 0.4
        ):
            character_approved_count += 1
            continue

    return character_approved_count / len(ordered_characters)


def alpha_unicode_split(decoded_sequence: str) -> List[str]:
    \"\"\"
    Given a decoded text sequence, return a list of str. Unicode range / alphabet separation.
    Ex. a text containing English/Latin with a bit a Hebrew will return two items in the resulting list;
    One containing the latin letters and the other hebrew.
    \"\"\"
    layers: Dict[str, str] = {}

    for character in decoded_sequence:
        if character.isalpha() is False:
            continue

        character_range: Optional[str] = unicode_range(character)

        if character_range is None:
            continue

        layer_target_range: Optional[str] = None

        for discovered_range in layers:
            if (
                is_suspiciously_successive_range(discovered_range, character_range)
                is False
            ):
                layer_target_range = discovered_range
                break

        if layer_target_range is None:
            layer_target_range = character_range

        if layer_target_range not in layers:
            layers[layer_target_range] = character.lower()
            continue

        layers[layer_target_range] += character.lower()

    return list(layers.values())


def merge_coherence_ratios(results: List[CoherenceMatches]) -> CoherenceMatches:
    \"\"\"
    This function merge results previously given by the function coherence_ratio.
    The return type is the same as coherence_ratio.
    \"\"\"
    per_language_ratios: Dict[str, List[float]] = {}
    for result in results:
        for sub_result in result:
            language, ratio = sub_result
            if language not in per_language_ratios:
                per_language_ratios[language] = [ratio]
                continue
            per_language_ratios[language].append(ratio)

    merge = [
        (
            language,
            round(
                sum(per_language_ratios[language]) / len(per_language_ratios[language]),
                4,
            ),
        )
        for language in per_language_ratios
    ]

    return sorted(merge, key=lambda x: x[1], reverse=True)


@lru_cache(maxsize=2048)
def coherence_ratio(
    decoded_sequence: str, threshold: float = 0.1, lg_inclusion: Optional[str] = None
) -> CoherenceMatches:
    \"\"\"
    Detect ANY language that can be identified in given sequence. The sequence will be analysed by layers.
    A layer = Character extraction by alphabets/ranges.
    \"\"\"

    results: List[Tuple[str, float]] = []
    ignore_non_latin: bool = False

    sufficient_match_count: int = 0

    lg_inclusion_list = lg_inclusion.split(\",\") if lg_inclusion is not None else []
    if \"Latin Based\" in lg_inclusion_list:
        ignore_non_latin = True
        lg_inclusion_list.remove(\"Latin Based\")

    for layer in alpha_unicode_split(decoded_sequence):
        sequence_frequencies: Counter = Counter(layer)
        most_common = sequence_frequencies.most_common()

        character_count: int = sum(o for c, o in most_common)

        if character_count <= TOO_SMALL_SEQUENCE:
            continue

        popular_character_ordered: List[str] = [c for c, o in most_common]

        for language in lg_inclusion_list or alphabet_languages(
            popular_character_ordered, ignore_non_latin
        ):
            ratio: float = characters_popularity_compare(
                language, popular_character_ordered
            )

            if ratio < threshold:
                continue
            elif ratio >= 0.8:
                sufficient_match_count += 1

            results.append((language, round(ratio, 4)))

            if sufficient_match_count >= 3:
                break

    return sorted(results, key=lambda x: x[1], reverse=True)

"""
module_dict["charset_normalizer"+os.sep+"assets"+os.sep+"__init__.py"]="""
# -*- coding: utf_8 -*-
from typing import Dict, List

FREQUENCIES: Dict[str, List[str]] = {
    \"English\": [
        \"e\",
        \"a\",
        \"t\",
        \"i\",
        \"o\",
        \"n\",
        \"s\",
        \"r\",
        \"h\",
        \"l\",
        \"d\",
        \"c\",
        \"u\",
        \"m\",
        \"f\",
        \"p\",
        \"g\",
        \"w\",
        \"y\",
        \"b\",
        \"v\",
        \"k\",
        \"x\",
        \"j\",
        \"z\",
        \"q\",
    ],
    \"German\": [
        \"e\",
        \"n\",
        \"i\",
        \"r\",
        \"s\",
        \"t\",
        \"a\",
        \"d\",
        \"h\",
        \"u\",
        \"l\",
        \"g\",
        \"o\",
        \"c\",
        \"m\",
        \"b\",
        \"f\",
        \"k\",
        \"w\",
        \"z\",
        \"p\",
        \"v\",
        \"ü\",
        \"ä\",
        \"ö\",
        \"j\",
    ],
    \"French\": [
        \"e\",
        \"a\",
        \"s\",
        \"n\",
        \"i\",
        \"t\",
        \"r\",
        \"l\",
        \"u\",
        \"o\",
        \"d\",
        \"c\",
        \"p\",
        \"m\",
        \"é\",
        \"v\",
        \"g\",
        \"f\",
        \"b\",
        \"h\",
        \"q\",
        \"à\",
        \"x\",
        \"è\",
        \"y\",
        \"j\",
    ],
    \"Dutch\": [
        \"e\",
        \"n\",
        \"a\",
        \"i\",
        \"r\",
        \"t\",
        \"o\",
        \"d\",
        \"s\",
        \"l\",
        \"g\",
        \"h\",
        \"v\",
        \"m\",
        \"u\",
        \"k\",
        \"c\",
        \"p\",
        \"b\",
        \"w\",
        \"j\",
        \"z\",
        \"f\",
        \"y\",
        \"x\",
        \"ë\",
    ],
    \"Italian\": [
        \"e\",
        \"i\",
        \"a\",
        \"o\",
        \"n\",
        \"l\",
        \"t\",
        \"r\",
        \"s\",
        \"c\",
        \"d\",
        \"u\",
        \"p\",
        \"m\",
        \"g\",
        \"v\",
        \"f\",
        \"b\",
        \"z\",
        \"h\",
        \"q\",
        \"è\",
        \"à\",
        \"k\",
        \"y\",
        \"ò\",
    ],
    \"Polish\": [
        \"a\",
        \"i\",
        \"o\",
        \"e\",
        \"n\",
        \"r\",
        \"z\",
        \"w\",
        \"s\",
        \"c\",
        \"t\",
        \"k\",
        \"y\",
        \"d\",
        \"p\",
        \"m\",
        \"u\",
        \"l\",
        \"j\",
        \"ł\",
        \"g\",
        \"b\",
        \"h\",
        \"ą\",
        \"ę\",
        \"ó\",
    ],
    \"Spanish\": [
        \"e\",
        \"a\",
        \"o\",
        \"n\",
        \"s\",
        \"r\",
        \"i\",
        \"l\",
        \"d\",
        \"t\",
        \"c\",
        \"u\",
        \"m\",
        \"p\",
        \"b\",
        \"g\",
        \"v\",
        \"f\",
        \"y\",
        \"ó\",
        \"h\",
        \"q\",
        \"í\",
        \"j\",
        \"z\",
        \"á\",
    ],
    \"Russian\": [
        \"о\",
        \"а\",
        \"е\",
        \"и\",
        \"н\",
        \"с\",
        \"т\",
        \"р\",
        \"в\",
        \"л\",
        \"к\",
        \"м\",
        \"д\",
        \"п\",
        \"у\",
        \"г\",
        \"я\",
        \"ы\",
        \"з\",
        \"б\",
        \"й\",
        \"ь\",
        \"ч\",
        \"х\",
        \"ж\",
        \"ц\",
    ],
    \"Japanese\": [
        \"の\",
        \"に\",
        \"る\",
        \"た\",
        \"は\",
        \"ー\",
        \"と\",
        \"し\",
        \"を\",
        \"で\",
        \"て\",
        \"が\",
        \"い\",
        \"ン\",
        \"れ\",
        \"な\",
        \"年\",
        \"ス\",
        \"っ\",
        \"ル\",
        \"か\",
        \"ら\",
        \"あ\",
        \"さ\",
        \"も\",
        \"り\",
    ],
    \"Portuguese\": [
        \"a\",
        \"e\",
        \"o\",
        \"s\",
        \"i\",
        \"r\",
        \"d\",
        \"n\",
        \"t\",
        \"m\",
        \"u\",
        \"c\",
        \"l\",
        \"p\",
        \"g\",
        \"v\",
        \"b\",
        \"f\",
        \"h\",
        \"ã\",
        \"q\",
        \"é\",
        \"ç\",
        \"á\",
        \"z\",
        \"í\",
    ],
    \"Swedish\": [
        \"e\",
        \"a\",
        \"n\",
        \"r\",
        \"t\",
        \"s\",
        \"i\",
        \"l\",
        \"d\",
        \"o\",
        \"m\",
        \"k\",
        \"g\",
        \"v\",
        \"h\",
        \"f\",
        \"u\",
        \"p\",
        \"ä\",
        \"c\",
        \"b\",
        \"ö\",
        \"å\",
        \"y\",
        \"j\",
        \"x\",
    ],
    \"Chinese\": [
        \"的\",
        \"一\",
        \"是\",
        \"不\",
        \"了\",
        \"在\",
        \"人\",
        \"有\",
        \"我\",
        \"他\",
        \"这\",
        \"个\",
        \"们\",
        \"中\",
        \"来\",
        \"上\",
        \"大\",
        \"为\",
        \"和\",
        \"国\",
        \"地\",
        \"到\",
        \"以\",
        \"说\",
        \"时\",
        \"要\",
        \"就\",
        \"出\",
        \"会\",
    ],
    \"Ukrainian\": [
        \"о\",
        \"а\",
        \"н\",
        \"і\",
        \"и\",
        \"р\",
        \"в\",
        \"т\",
        \"е\",
        \"с\",
        \"к\",
        \"л\",
        \"у\",
        \"д\",
        \"м\",
        \"п\",
        \"з\",
        \"я\",
        \"ь\",
        \"б\",
        \"г\",
        \"й\",
        \"ч\",
        \"х\",
        \"ц\",
        \"ї\",
    ],
    \"Norwegian\": [
        \"e\",
        \"r\",
        \"n\",
        \"t\",
        \"a\",
        \"s\",
        \"i\",
        \"o\",
        \"l\",
        \"d\",
        \"g\",
        \"k\",
        \"m\",
        \"v\",
        \"f\",
        \"p\",
        \"u\",
        \"b\",
        \"h\",
        \"å\",
        \"y\",
        \"j\",
        \"ø\",
        \"c\",
        \"æ\",
        \"w\",
    ],
    \"Finnish\": [
        \"a\",
        \"i\",
        \"n\",
        \"t\",
        \"e\",
        \"s\",
        \"l\",
        \"o\",
        \"u\",
        \"k\",
        \"ä\",
        \"m\",
        \"r\",
        \"v\",
        \"j\",
        \"h\",
        \"p\",
        \"y\",
        \"d\",
        \"ö\",
        \"g\",
        \"c\",
        \"b\",
        \"f\",
        \"w\",
        \"z\",
    ],
    \"Vietnamese\": [
        \"n\",
        \"h\",
        \"t\",
        \"i\",
        \"c\",
        \"g\",
        \"a\",
        \"o\",
        \"u\",
        \"m\",
        \"l\",
        \"r\",
        \"à\",
        \"đ\",
        \"s\",
        \"e\",
        \"v\",
        \"p\",
        \"b\",
        \"y\",
        \"ư\",
        \"d\",
        \"á\",
        \"k\",
        \"ộ\",
        \"ế\",
    ],
    \"Czech\": [
        \"o\",
        \"e\",
        \"a\",
        \"n\",
        \"t\",
        \"s\",
        \"i\",
        \"l\",
        \"v\",
        \"r\",
        \"k\",
        \"d\",
        \"u\",
        \"m\",
        \"p\",
        \"í\",
        \"c\",
        \"h\",
        \"z\",
        \"á\",
        \"y\",
        \"j\",
        \"b\",
        \"ě\",
        \"é\",
        \"ř\",
    ],
    \"Hungarian\": [
        \"e\",
        \"a\",
        \"t\",
        \"l\",
        \"s\",
        \"n\",
        \"k\",
        \"r\",
        \"i\",
        \"o\",
        \"z\",
        \"á\",
        \"é\",
        \"g\",
        \"m\",
        \"b\",
        \"y\",
        \"v\",
        \"d\",
        \"h\",
        \"u\",
        \"p\",
        \"j\",
        \"ö\",
        \"f\",
        \"c\",
    ],
    \"Korean\": [
        \"이\",
        \"다\",
        \"에\",
        \"의\",
        \"는\",
        \"로\",
        \"하\",
        \"을\",
        \"가\",
        \"고\",
        \"지\",
        \"서\",
        \"한\",
        \"은\",
        \"기\",
        \"으\",
        \"년\",
        \"대\",
        \"사\",
        \"시\",
        \"를\",
        \"리\",
        \"도\",
        \"인\",
        \"스\",
        \"일\",
    ],
    \"Indonesian\": [
        \"a\",
        \"n\",
        \"e\",
        \"i\",
        \"r\",
        \"t\",
        \"u\",
        \"s\",
        \"d\",
        \"k\",
        \"m\",
        \"l\",
        \"g\",
        \"p\",
        \"b\",
        \"o\",
        \"h\",
        \"y\",
        \"j\",
        \"c\",
        \"w\",
        \"f\",
        \"v\",
        \"z\",
        \"x\",
        \"q\",
    ],
    \"Turkish\": [
        \"a\",
        \"e\",
        \"i\",
        \"n\",
        \"r\",
        \"l\",
        \"ı\",
        \"k\",
        \"d\",
        \"t\",
        \"s\",
        \"m\",
        \"y\",
        \"u\",
        \"o\",
        \"b\",
        \"ü\",
        \"ş\",
        \"v\",
        \"g\",
        \"z\",
        \"h\",
        \"c\",
        \"p\",
        \"ç\",
        \"ğ\",
    ],
    \"Romanian\": [
        \"e\",
        \"i\",
        \"a\",
        \"r\",
        \"n\",
        \"t\",
        \"u\",
        \"l\",
        \"o\",
        \"c\",
        \"s\",
        \"d\",
        \"p\",
        \"m\",
        \"ă\",
        \"f\",
        \"v\",
        \"î\",
        \"g\",
        \"b\",
        \"ș\",
        \"ț\",
        \"z\",
        \"h\",
        \"â\",
        \"j\",
    ],
    \"Farsi\": [
        \"ا\",
        \"ی\",
        \"ر\",
        \"د\",
        \"ن\",
        \"ه\",
        \"و\",
        \"م\",
        \"ت\",
        \"ب\",
        \"س\",
        \"ل\",
        \"ک\",
        \"ش\",
        \"ز\",
        \"ف\",
        \"گ\",
        \"ع\",
        \"خ\",
        \"ق\",
        \"ج\",
        \"آ\",
        \"پ\",
        \"ح\",
        \"ط\",
        \"ص\",
    ],
    \"Arabic\": [
        \"ا\",
        \"ل\",
        \"ي\",
        \"م\",
        \"و\",
        \"ن\",
        \"ر\",
        \"ت\",
        \"ب\",
        \"ة\",
        \"ع\",
        \"د\",
        \"س\",
        \"ف\",
        \"ه\",
        \"ك\",
        \"ق\",
        \"أ\",
        \"ح\",
        \"ج\",
        \"ش\",
        \"ط\",
        \"ص\",
        \"ى\",
        \"خ\",
        \"إ\",
    ],
    \"Danish\": [
        \"e\",
        \"r\",
        \"n\",
        \"t\",
        \"a\",
        \"i\",
        \"s\",
        \"d\",
        \"l\",
        \"o\",
        \"g\",
        \"m\",
        \"k\",
        \"f\",
        \"v\",
        \"u\",
        \"b\",
        \"h\",
        \"p\",
        \"å\",
        \"y\",
        \"ø\",
        \"æ\",
        \"c\",
        \"j\",
        \"w\",
    ],
    \"Serbian\": [
        \"а\",
        \"и\",
        \"о\",
        \"е\",
        \"н\",
        \"р\",
        \"с\",
        \"у\",
        \"т\",
        \"к\",
        \"ј\",
        \"в\",
        \"д\",
        \"м\",
        \"п\",
        \"л\",
        \"г\",
        \"з\",
        \"б\",
        \"a\",
        \"i\",
        \"e\",
        \"o\",
        \"n\",
        \"ц\",
        \"ш\",
    ],
    \"Lithuanian\": [
        \"i\",
        \"a\",
        \"s\",
        \"o\",
        \"r\",
        \"e\",
        \"t\",
        \"n\",
        \"u\",
        \"k\",
        \"m\",
        \"l\",
        \"p\",
        \"v\",
        \"d\",
        \"j\",
        \"g\",
        \"ė\",
        \"b\",
        \"y\",
        \"ų\",
        \"š\",
        \"ž\",
        \"c\",
        \"ą\",
        \"į\",
    ],
    \"Slovene\": [
        \"e\",
        \"a\",
        \"i\",
        \"o\",
        \"n\",
        \"r\",
        \"s\",
        \"l\",
        \"t\",
        \"j\",
        \"v\",
        \"k\",
        \"d\",
        \"p\",
        \"m\",
        \"u\",
        \"z\",
        \"b\",
        \"g\",
        \"h\",
        \"č\",
        \"c\",
        \"š\",
        \"ž\",
        \"f\",
        \"y\",
    ],
    \"Slovak\": [
        \"o\",
        \"a\",
        \"e\",
        \"n\",
        \"i\",
        \"r\",
        \"v\",
        \"t\",
        \"s\",
        \"l\",
        \"k\",
        \"d\",
        \"m\",
        \"p\",
        \"u\",
        \"c\",
        \"h\",
        \"j\",
        \"b\",
        \"z\",
        \"á\",
        \"y\",
        \"ý\",
        \"í\",
        \"č\",
        \"é\",
    ],
    \"Hebrew\": [
        \"י\",
        \"ו\",
        \"ה\",
        \"ל\",
        \"ר\",
        \"ב\",
        \"ת\",
        \"מ\",
        \"א\",
        \"ש\",
        \"נ\",
        \"ע\",
        \"ם\",
        \"ד\",
        \"ק\",
        \"ח\",
        \"פ\",
        \"ס\",
        \"כ\",
        \"ג\",
        \"ט\",
        \"צ\",
        \"ן\",
        \"ז\",
        \"ך\",
    ],
    \"Bulgarian\": [
        \"а\",
        \"и\",
        \"о\",
        \"е\",
        \"н\",
        \"т\",
        \"р\",
        \"с\",
        \"в\",
        \"л\",
        \"к\",
        \"д\",
        \"п\",
        \"м\",
        \"з\",
        \"г\",
        \"я\",
        \"ъ\",
        \"у\",
        \"б\",
        \"ч\",
        \"ц\",
        \"й\",
        \"ж\",
        \"щ\",
        \"х\",
    ],
    \"Croatian\": [
        \"a\",
        \"i\",
        \"o\",
        \"e\",
        \"n\",
        \"r\",
        \"j\",
        \"s\",
        \"t\",
        \"u\",
        \"k\",
        \"l\",
        \"v\",
        \"d\",
        \"m\",
        \"p\",
        \"g\",
        \"z\",
        \"b\",
        \"c\",
        \"č\",
        \"h\",
        \"š\",
        \"ž\",
        \"ć\",
        \"f\",
    ],
    \"Hindi\": [
        \"क\",
        \"र\",
        \"स\",
        \"न\",
        \"त\",
        \"म\",
        \"ह\",
        \"प\",
        \"य\",
        \"ल\",
        \"व\",
        \"ज\",
        \"द\",
        \"ग\",
        \"ब\",
        \"श\",
        \"ट\",
        \"अ\",
        \"ए\",
        \"थ\",
        \"भ\",
        \"ड\",
        \"च\",
        \"ध\",
        \"ष\",
        \"इ\",
    ],
    \"Estonian\": [
        \"a\",
        \"i\",
        \"e\",
        \"s\",
        \"t\",
        \"l\",
        \"u\",
        \"n\",
        \"o\",
        \"k\",
        \"r\",
        \"d\",
        \"m\",
        \"v\",
        \"g\",
        \"p\",
        \"j\",
        \"h\",
        \"ä\",
        \"b\",
        \"õ\",
        \"ü\",
        \"f\",
        \"c\",
        \"ö\",
        \"y\",
    ],
    \"Simple English\": [
        \"e\",
        \"a\",
        \"t\",
        \"i\",
        \"o\",
        \"n\",
        \"s\",
        \"r\",
        \"h\",
        \"l\",
        \"d\",
        \"c\",
        \"m\",
        \"u\",
        \"f\",
        \"p\",
        \"g\",
        \"w\",
        \"b\",
        \"y\",
        \"v\",
        \"k\",
        \"j\",
        \"x\",
        \"z\",
        \"q\",
    ],
    \"Thai\": [
        \"า\",
        \"น\",
        \"ร\",
        \"อ\",
        \"ก\",
        \"เ\",
        \"ง\",
        \"ม\",
        \"ย\",
        \"ล\",
        \"ว\",
        \"ด\",
        \"ท\",
        \"ส\",
        \"ต\",
        \"ะ\",
        \"ป\",
        \"บ\",
        \"ค\",
        \"ห\",
        \"แ\",
        \"จ\",
        \"พ\",
        \"ช\",
        \"ข\",
        \"ใ\",
    ],
    \"Greek\": [
        \"α\",
        \"τ\",
        \"ο\",
        \"ι\",
        \"ε\",
        \"ν\",
        \"ρ\",
        \"σ\",
        \"κ\",
        \"η\",
        \"π\",
        \"ς\",
        \"υ\",
        \"μ\",
        \"λ\",
        \"ί\",
        \"ό\",
        \"ά\",
        \"γ\",
        \"έ\",
        \"δ\",
        \"ή\",
        \"ω\",
        \"χ\",
        \"θ\",
        \"ύ\",
    ],
    \"Tamil\": [
        \"க\",
        \"த\",
        \"ப\",
        \"ட\",
        \"ர\",
        \"ம\",
        \"ல\",
        \"ன\",
        \"வ\",
        \"ற\",
        \"ய\",
        \"ள\",
        \"ச\",
        \"ந\",
        \"இ\",
        \"ண\",
        \"அ\",
        \"ஆ\",
        \"ழ\",
        \"ங\",
        \"எ\",
        \"உ\",
        \"ஒ\",
        \"ஸ\",
    ],
    \"Classical Chinese\": [
        \"之\",
        \"年\",
        \"為\",
        \"也\",
        \"以\",
        \"一\",
        \"人\",
        \"其\",
        \"者\",
        \"國\",
        \"有\",
        \"二\",
        \"十\",
        \"於\",
        \"曰\",
        \"三\",
        \"不\",
        \"大\",
        \"而\",
        \"子\",
        \"中\",
        \"五\",
        \"四\",
    ],
    \"Kazakh\": [
        \"а\",
        \"ы\",
        \"е\",
        \"н\",
        \"т\",
        \"р\",
        \"л\",
        \"і\",
        \"д\",
        \"с\",
        \"м\",
        \"қ\",
        \"к\",
        \"о\",
        \"б\",
        \"и\",
        \"у\",
        \"ғ\",
        \"ж\",
        \"ң\",
        \"з\",
        \"ш\",
        \"й\",
        \"п\",
        \"г\",
        \"ө\",
    ],
}

"""
module_dict["charset_normalizer"+os.sep+"cli"+os.sep+"normalizer.py"]="""
# < include 'unicodedata2.py' >

import argparse
import sys
from json import dumps
from os.path import abspath
from platform import python_version
from typing import List

try:
    from unicodedata2 import unidata_version
except ImportError:
    from unicodedata import unidata_version

from charset_normalizer import from_fp
from charset_normalizer.models import CliDetectionResult
from charset_normalizer.version import __version__


def query_yes_no(question: str, default: str = \"yes\") -> bool:
    \"\"\"Ask a yes/no question via input() and return their answer.

    \"question\" is a string that is presented to the user.
    \"default\" is the presumed answer if the user just hits <Enter>.
        It must be \"yes\" (the default), \"no\" or None (meaning
        an answer is required of the user).

    The \"answer\" return value is True for \"yes\" or False for \"no\".

    Credit goes to (c) https://stackoverflow.com/questions/3041986/apt-command-line-interface-like-yes-no-input
    \"\"\"
    valid = {\"yes\": True, \"y\": True, \"ye\": True, \"no\": False, \"n\": False}
    if default is None:
        prompt = \" [y/n] \"
    elif default == \"yes\":
        prompt = \" [Y/n] \"
    elif default == \"no\":
        prompt = \" [y/N] \"
    else:
        raise ValueError(\"invalid default answer: '%s'\" % default)

    while True:
        sys.stdout.write(question + prompt)
        choice = input().lower()
        if default is not None and choice == \"\":
            return valid[default]
        elif choice in valid:
            return valid[choice]
        else:
            sys.stdout.write(\"Please respond with 'yes' or 'no' \" \"(or 'y' or 'n').\\n\")


def cli_detect(argv: List[str] = None) -> int:
    \"\"\"
    CLI assistant using ARGV and ArgumentParser
    :param argv:
    :return: 0 if everything is fine, anything else equal trouble
    \"\"\"
    parser = argparse.ArgumentParser(
        description=\"The Real First Universal Charset Detector. \"
        \"Discover originating encoding used on text file. \"
        \"Normalize text to unicode.\"
    )

    parser.add_argument(
        \"files\", type=argparse.FileType(\"rb\"), nargs=\"+\", help=\"File(s) to be analysed\"
    )
    parser.add_argument(
        \"-v\",
        \"--verbose\",
        action=\"store_true\",
        default=False,
        dest=\"verbose\",
        help=\"Display complementary information about file if any. \"
        \"Stdout will contain logs about the detection process.\",
    )
    parser.add_argument(
        \"-a\",
        \"--with-alternative\",
        action=\"store_true\",
        default=False,
        dest=\"alternatives\",
        help=\"Output complementary possibilities if any. Top-level JSON WILL be a list.\",
    )
    parser.add_argument(
        \"-n\",
        \"--normalize\",
        action=\"store_true\",
        default=False,
        dest=\"normalize\",
        help=\"Permit to normalize input file. If not set, program does not write anything.\",
    )
    parser.add_argument(
        \"-m\",
        \"--minimal\",
        action=\"store_true\",
        default=False,
        dest=\"minimal\",
        help=\"Only output the charset detected to STDOUT. Disabling JSON output.\",
    )
    parser.add_argument(
        \"-r\",
        \"--replace\",
        action=\"store_true\",
        default=False,
        dest=\"replace\",
        help=\"Replace file when trying to normalize it instead of creating a new one.\",
    )
    parser.add_argument(
        \"-f\",
        \"--force\",
        action=\"store_true\",
        default=False,
        dest=\"force\",
        help=\"Replace file without asking if you are sure, use this flag with caution.\",
    )
    parser.add_argument(
        \"-t\",
        \"--threshold\",
        action=\"store\",
        default=0.2,
        type=float,
        dest=\"threshold\",
        help=\"Define a custom maximum amount of chaos allowed in decoded content. 0. <= chaos <= 1.\",
    )
    parser.add_argument(
        \"--version\",
        action=\"version\",
        version=\"Charset-Normalizer {} - Python {} - Unicode {}\".format(
            __version__, python_version(), unidata_version
        ),
        help=\"Show version information and exit.\",
    )

    args = parser.parse_args(argv)

    if args.replace is True and args.normalize is False:
        print(\"Use --replace in addition of --normalize only.\", file=sys.stderr)
        return 1

    if args.force is True and args.replace is False:
        print(\"Use --force in addition of --replace only.\", file=sys.stderr)
        return 1

    if args.threshold < 0.0 or args.threshold > 1.0:
        print(\"--threshold VALUE should be between 0. AND 1.\", file=sys.stderr)
        return 1

    x_ = []

    for my_file in args.files:

        matches = from_fp(my_file, threshold=args.threshold, explain=args.verbose)

        best_guess = matches.best()

        if best_guess is None:
            print(
                'Unable to identify originating encoding for \"{}\". {}'.format(
                    my_file.name,
                    \"Maybe try increasing maximum amount of chaos.\"
                    if args.threshold < 1.0
                    else \"\",
                ),
                file=sys.stderr,
            )
            x_.append(
                CliDetectionResult(
                    abspath(my_file.name),
                    None,
                    [],
                    [],
                    \"Unknown\",
                    [],
                    False,
                    1.0,
                    0.0,
                    None,
                    True,
                )
            )
        else:
            x_.append(
                CliDetectionResult(
                    abspath(my_file.name),
                    best_guess.encoding,
                    best_guess.encoding_aliases,
                    [
                        cp
                        for cp in best_guess.could_be_from_charset
                        if cp != best_guess.encoding
                    ],
                    best_guess.language,
                    best_guess.alphabets,
                    best_guess.bom,
                    best_guess.percent_chaos,
                    best_guess.percent_coherence,
                    None,
                    True,
                )
            )

            if len(matches) > 1 and args.alternatives:
                for el in matches:
                    if el != best_guess:
                        x_.append(
                            CliDetectionResult(
                                abspath(my_file.name),
                                el.encoding,
                                el.encoding_aliases,
                                [
                                    cp
                                    for cp in el.could_be_from_charset
                                    if cp != el.encoding
                                ],
                                el.language,
                                el.alphabets,
                                el.bom,
                                el.percent_chaos,
                                el.percent_coherence,
                                None,
                                False,
                            )
                        )

            if args.normalize is True:

                if best_guess.encoding.startswith(\"utf\") is True:
                    print(
                        '\"{}\" file does not need to be normalized, as it already came from unicode.'.format(
                            my_file.name
                        ),
                        file=sys.stderr,
                    )
                    if my_file.closed is False:
                        my_file.close()
                    continue

                o_: List[str] = my_file.name.split(\".\")

                if args.replace is False:
                    o_.insert(-1, best_guess.encoding)
                    if my_file.closed is False:
                        my_file.close()
                elif (
                    args.force is False
                    and query_yes_no(
                        'Are you sure to normalize \"{}\" by replacing it ?'.format(
                            my_file.name
                        ),
                        \"no\",
                    )
                    is False
                ):
                    if my_file.closed is False:
                        my_file.close()
                    continue

                try:
                    x_[0].unicode_path = abspath(\"./{}\".format(\".\".join(o_)))

                    with open(x_[0].unicode_path, \"w\", encoding=\"utf-8\") as fp:
                        fp.write(str(best_guess))
                except IOError as e:
                    print(str(e), file=sys.stderr)
                    if my_file.closed is False:
                        my_file.close()
                    return 2

        if my_file.closed is False:
            my_file.close()

    if args.minimal is False:
        print(
            dumps(
                [el.__dict__ for el in x_] if len(x_) > 1 else x_[0].__dict__,
                ensure_ascii=True,
                indent=4,
            )
        )
    else:
        for my_file in args.files:
            print(
                \", \".join(
                    [
                        el.encoding or \"undefined\"
                        for el in x_
                        if el.path == abspath(my_file.name)
                    ]
                )
            )

    return 0


if __name__ == \"__main__\":
    cli_detect()

"""
module_dict["charset_normalizer"+os.sep+"cli"+os.sep+"__init__.py"]="""

"""

import os
import types
import zipfile
import sys
import io
import json

class ZipImporter(object):
    def __init__(self, zip_file):
        self.zfile = zip_file
        self._paths = [x.filename for x in self.zfile.filelist]
        
    def _mod_to_paths(self, fullname):
        # get the python module name
        py_filename = fullname.replace(".", os.sep) + ".py"
        # get the filename if it is a package/subpackage
        py_package = fullname.replace(".", os.sep) + os.sep + "__init__.py"
        if py_filename in self._paths:
            return py_filename
        elif py_package in self._paths:
            return py_package
        else:
            return None

    def find_module(self, fullname, path):
        if self._mod_to_paths(fullname) is not None:
            return self
        return None

    def load_module(self, fullname):
        filename = self._mod_to_paths(fullname)
        if not filename in self._paths:
            raise ImportError(fullname)
        new_module = types.ModuleType(fullname)
        sys.modules[fullname]=new_module
        if filename.endswith("__init__.py"):
            new_module.__path__ = [] 
            new_module.__package__ = fullname
        else:
            new_module.__package__ = fullname.rpartition('.')[0]
        exec(self.zfile.open(filename, 'r').read(),new_module.__dict__)
        new_module.__file__ = filename
        new_module.__loader__ = self
        new_module.__spec__=json.__spec__ # To satisfy importlib._common.get_package
        return new_module

module_zip=zipfile.ZipFile(io.BytesIO(),"w")
for key in module_dict:
    module_zip.writestr(key,module_dict[key])

module_importer=ZipImporter(module_zip)
sys.meta_path.insert(0,module_importer)

#from charset_normalizer import *
import charset_normalizer
globals().update(charset_normalizer.__dict__)
    
if module_importer in sys.meta_path:
    sys.meta_path.remove(module_importer)

#for key in sys.modules.copy():
#    if key=="charset_normalizer" or key.startswith("charset_normalizer."):
#        del sys.modules[key]
