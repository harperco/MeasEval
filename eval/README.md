#### Welcome to the MeasEval Evaluation Code

This is a modified version of the evaluation code running on [our CodaLab competition](https://competitions.codalab.org/competitions/25770). The modifications are minor, and are for the purpose of running this codebase locally to test your models prior to submission.

* While the CodaLab version manages input and output per CodaLab practices, we are using argparse locally. This lets you specify the base path of your installation, subdirectories of your gold and "submission" data, and a mode to run in. See "Advanced Options" below for further documentation of these options.
* The CodaLab copy always runs in "Overall" mode, which is also the default here.
* The CodaLab copy evaluates your submission against the entire gold set. Locally, evaluation is limited to paragraph IDs that match your submission. This allows you to use any portion of the training data as holdout when training models and evaluate on that portion of the data.

#### Installation

We are using Python 3, vladiate for validation of submission .tsv files, and pandasql to handle to handle Theta joins necessary for matching and subsetting dataframes. We also rely on Pandas features that require Pandas >= 1.0.

To install necessary libraries, set up a virtual environment of your choice and run `pip install -r requirements.txt` from the "eval" directory of your MeasEval clone.

#### Quick Start

From the installation directory, run the evaluation as:

`python measeval-eval.py -i /path/to/measeval/data/ -s sub/ -g gold/`

Where -i is the full path to the MeasEval data, -s is your submission subdirectory or path, and -g is the subdirectory or path to your download of the gold data. -s and -g must both be subdirectories of the path listed in -i. This processes your submission files against corresponding gold files, but will ignore gold files for paragraphs not included in your submission, allowing you to just evaluate the test data from your train/test split.

If your .tsv files pass validation, the script will proceed with evaluation. If your files do not pass validation, the script will exit and tell you which files failed validation and what the corresponding errors are.

Evaluation output includes counts of true positives, false positive, and false negatives, calculates a precision, recall, and f-measure, and gives an Exact Match (EM) and SQuAD-style F1 (Overlap) score for your submission. The overall F1 (Overlap) score is the single single score on the CodaLab leaderboard.

#### Advanced Options

There are 2 additional optional arguments you can pass:

* -m (--mode) allows further control over how scores are averaged. Options are "overall" (the default), "class", "doc", or "both". The "class" option gives you all the same metrics averaged for each of the 9 specific scoring components (Quantity, MeasuredProperty, MeasuredEntity, Qualifier, Unit, Modifiers, HasQuantity, HasProperty, and Qualifies); "doc" provides the averages broken down by paragraph ID, and "both" provides a very detailed breakdown of each score by class and by paragraph.
* --skip allows you to provide a text file, in the project directory, with one .tsv **filename** per line listing files you may wish to exclude from evaluation for whatever reason.

This should help you identify particular areas or documents where your model is not performing as well as you'd like and further tune your training.

#### Evaluation Algorithm Overview

In order to effectively evaluate all 9 components of our sub-tasks, it is necessary to first pin all entries in a submission to the corresponding entities in the gold data. Given the sentence, "The dog weighed 25 pounds, while the average weight of the cats was 9 lbs.", for example, we want to avoid crediting correct MeasuredEntities if associated with the wrong Quantity. For example, if a submission listed "dog" as the MeasuredEntity associated with the average weight of 9 lbs, this would be incorrect.

The first pass matches each submission "AnnotSet" id to a corresponding Gold Set annotation id, and propagates this match ID across all of the data.

From there, we evaluate matches for each of the score components. Exact Match is a binary value of 0 or 1, while F1 is a token level overlap ratio of submission to gold spans, where tokenization is done using simple white space delimiters. For components that do not include a span, Exact Match and F1 scores are the same. Relations are also scored with a binary match score if the relation types match and both endpoints match either exactly or with some overlap.

Any span, unit, modifier, or relationship found in the gold data but not the submission, or found in the submission but not the gold data is included as a "penalty row" with a score of 0 in order to sufficiently penalize both false positives and false negatives when averaging scores.

The script is documented thoroughly if you would like to review the algorithm in more detail. If you have any questions, comments or issues, please contact the organizers [measeval-organizers@googlegroups.com](mailto:measeval-organizers@googlegroups.com) or reach out on the competition Google Group [https://groups.google.com/forum/#!forum/measeval-semeval-2021](https://groups.google.com/forum/#!forum/measeval-semeval-2021)

Special thanks to Co-Organizer Curt Kohler for his leadership on the scoring algorithm, as well as its original Spark Scala implementation.
