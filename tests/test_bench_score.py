from bench.stt.score import edit_distance, normalize


def test_turkish_dotted_and_dotless_i_lowercase_correctly():
    assert normalize("IŞIK İstanbul'da", "tr") == "ışık istanbulda"


def test_arabic_marks_and_alef_forms_are_normalized():
    assert normalize("مُسماة إلى", "ar") == normalize("مسماة الى", "ar")


def test_punctuation_is_ignored():
    assert normalize("Hello, world! (yes) — 25%", "en") == "hello world yes 25"


def test_edit_distance_counts_word_substitutions_insertions_deletions():
    assert edit_distance("a b c".split(), "a x c d".split()) == 2
    assert edit_distance([], "a b".split()) == 2
    assert edit_distance("a b".split(), []) == 2


def test_arabic_ta_marbuta_and_arabic_indic_digits_are_normalized():
    assert normalize("مدينة ٢٥", "ar") == normalize("مدينه 25", "ar")


def test_turkish_circumflex_is_optional():
    assert normalize("hâlâ", "tr") == normalize("hala", "tr")
