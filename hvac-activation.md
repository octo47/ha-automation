I had the same need and solved it differently by using triggers state

To SWITCH ON your central heating

  - platform: state
    entity_id:
      - climate.vanne_e1_salon
    attribute: hvac_action
    from: idle
    to: heating
    for:
      hours: 0
      minutes: 0
      seconds: 10
you repeat the trigger for each of your TRV you want to trigger your central heating

To SWITCH OFF your central heating,
you do the opposite with a trigger for each TRV

  - platform: state
    entity_id:
      - climate.vanne_e1_salon
    attribute: hvac_action
    from: heating
    to: idle
    for:
      hours: 0
      minutes: 0
      seconds: 10
And add a condition also for each TRV

condition:
  - condition: state
    entity_id: climate.vanne_e1_salon
    attribute: hvac_action
    state: idle
in other word you transform a OR for your trigger into a AND

you need for any TRV that goes to ‘heat’ (OR) → Turn on your central heating

you need a TRV to go to ‘idle’ if all others are already in ‘idle’ (AND) → Turn off your central heating