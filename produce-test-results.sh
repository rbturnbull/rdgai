LLM=claude-3-5-sonnet-20241022
EXAMPLES=30
rdgai validate \
    tests/test-data/arb.xml \
    results/arb-${EXAMPLES}-${LLM}.xml \
    --examples $EXAMPLES \
    --llm ${LLM} \
    --confusion-matrix-plot results/arb-${EXAMPLES}-${LLM}.confusion.pdf \
    --report results/arb-${EXAMPLES}-${LLM}.html