package cyberteam.action

import rego.v1

default allowed := false

default requires_approval := false

reasons contains reason if {
    some reason in input.hard_gate_reasons
}

requires_approval if {
    count(reasons) > 0
    not "prompt_injection_quarantine" in reasons
}

allowed if {
    count(reasons) == 0
}

allowed if {
    requires_approval
    input.approval_present == true
    not "prompt_injection_quarantine" in reasons
}

decision := {
    "allowed": allowed,
    "requires_approval": requires_approval,
    "reasons": sort([reason | some reason in reasons]),
}
