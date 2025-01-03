=====================
Why use Rdgai?
=====================

Not all textual variants were created equal. 
Some types of changes in variant readings, such as orthographic changes, may have occurred at many independent times across a tradition. 
Other changes are so substantial that they were introduced only on a single occasion. 
The application of phylogenetic methods to textual traditions has traditionally treated all changes as equivalent. 
If frequently occurring variations are weighted the same as strongly informative readings, then the true phylogenetic signal may be drowned in noise. 
Worse, systematic bias may distort the inference of relationships between textual witnesses. 
To mitigate this, types of variation deemed insignificant are often filtered out of phylogenetic analysis. 
However, this filtering can dramatically reduce the number of available variation units, and it is difficult to determine a priori which variants to exclude.

Probabilistic methods, such as Bayesian phylogenetics, allow users to create categories of changes, and the transition rates for each category can be estimated. 
This enables exploration of the scribes' habits across the branches of the tradition.

Classifying changes in variant readings not only improves phylogenetic analysis by allowing different substitution rates but also provides deeper insights into the transmission history. 
However, this is a laborious process requiring the manual classification of many thousands of textual changes. 
Automating this process presents a significant advantage.

One promising approach is using Large Language Models (LLMs). 
LLMs are artificial intelligence systems trained to predict the next token in a sequence. 
By leveraging their sophisticated representations of language, LLMs can be used for text classification tasks, including categorizing changes in textual readings.

Rdgai is a tool that facilitates the use of LLMs for classifying transitions between variant readings in a Text Encoding Initiative (TEI) XML file containing a critical apparatus.