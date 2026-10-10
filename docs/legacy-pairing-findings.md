# Pairing the tested legacy cooktop and wall oven

On the tested TP6X_CT_16K cooktop and NV51K777OS Flex Duo wall oven, token
pairing succeeded only with this sequence:

1. Keep the appliance's **Smart Connect** switch **off** before initiating
   pairing.
2. Request a token in **LocalThings** while Smart Connect is still off.
3. Switch Smart Connect **on after the token request**.

The owner's cooktop photographs show the label **Smart Connect**; earlier
notes called it Smart Control.

On both tested appliances, pairing did not return a token if Smart Connect
was already on when pairing began. This sequence has not been verified
for other TCP 8888 appliance models.
