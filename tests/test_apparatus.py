

def test_doc_print_classified_pairs(arb, capsys):
    arb.print_classified_pairs()
    captured = capsys.readouterr()
    assert "Single_Minor_Word_Change ───────────────────────────\nAn addition, omission" in captured.out
    assert "Jn8_12-7: الدهر بل تكون له ➞ الدهر بل يكون له\n" in captured.out