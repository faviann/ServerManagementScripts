# Mask vault-derived inventory values by character class

`./inspect.sh vars <host>` may print a masked derivative of a secret. It derives
the vault key/value set from the command's one merged inventory result. Every
value whose key starts with `vault_` is masked. Any other string containing a
vault scalar's rendered value of at least eight characters is masked whole,
which covers derived values such as bearer headers and numeric identifiers
without treating short settings such as `true` or `587` as secrets wherever
they occur.

The mask renders every letter as `a` without preserving case, every digit as
`9`, a space as `·`, a newline as `\n`, and a tab as `\t`. Other characters
remain literal. Runs longer than 16 identical letter or digit mask characters
collapse to the mask character and their count. This retains diagnostically
useful length, punctuation, and whitespace shape while withholding case as a
wordlist hint.

Fixed redaction was rejected because it hides the punctuation, whitespace, and
length faults this command exists to diagnose. Preserving case was rejected
because it reveals a useful word pattern without diagnosing an additional
fault. A closed exceptions or condition-flag list was rejected because a new
vault key could silently escape masking.

This decision is deliberately narrow. The `vault_` prefix is a required
top-level-key contract in the tracked vault example; a vault key missing that
prefix is outside the name rule. Secrets stored in plain group variables are
also outside the boundary. There is no reveal mode. The implementation remains
one pure mask function plus one masking pass over the existing merged-inventory
output, with no separate vault read or decryption.
