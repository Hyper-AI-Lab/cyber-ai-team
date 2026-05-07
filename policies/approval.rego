package cyberteam.approval

import rego.v1

# Default: no approval needed
default needs_approval := false

# Financial actions always need approval
needs_approval if {
    input.action_type == "payment"
}

needs_approval if {
    input.action_type == "invoice"
    input.amount > 1000
}

# Legal actions always need approval
needs_approval if {
    input.action_type == "contract"
}

needs_approval if {
    input.action_type == "nda"
}

# External communications to new contacts need approval
needs_approval if {
    input.action_type == "outbound_communication"
    input.is_first_contact == true
}

# HR actions need approval
needs_approval if {
    input.action_type == "hire"
}

needs_approval if {
    input.action_type == "terminate"
}

# Data deletion needs approval
needs_approval if {
    input.action_type == "delete"
    input.data_classification == "sensitive"
}
