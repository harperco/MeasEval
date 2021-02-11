import os
import argparse
import logging
import pandas as pd
from pandasql import sqldf
import json
from vladiate import Vlad, logs
from vladiate.validators import UniqueValidator, SetValidator, IntValidator, Validator, ValidationException, RegexValidator
from vladiate.inputs import LocalFile

# Set up argparse
parser = argparse.ArgumentParser(description='Takes output file, logfile config, secret')
parser.add_argument('-i','--indir', help='Input directory base path',required=True)
parser.add_argument('-g', '--gold', help='Gold data directory', required=True)
parser.add_argument('-s', '--sub', help='Submission data directory', required=True)
parser.add_argument('-m', '--mode', help='Mode to run scoring: overall, class, doc, classdoc (both), sub, or classsub; default is overall.', default="overall")
parser.add_argument('--skip', help='input file of files to skip for debugging, one id per line.')
parser.add_argument('-v', '--val', help='Validate submission only.', action='store_true')
parser.add_argument('-l', '--limit', help='Limit gold data loaded to files also in submission.', action='store_true')


args = parser.parse_args()

# Set pysqldf to keep global scope
pysqldf = lambda q: sqldf(q, globals())

# Load in the data

if args.skip is not None:
    with open(args.skip) as f:
        skip = f.read().splitlines()
else:
    skip = []

goldfs = []
subdfs = []
subnames = []

# We're using Vladiator to do some data validation on ingest
# Set up custom validator fucntions

# We'll have to change some logger criteria to get this to work as expected
# For validation. Can also make this a command line arg
# For now, uncomment if you want the more verbose logs
# logs.logger.setLevel(logging.DEBUG)
# logs.sh.setLevel(logging.DEBUG)

# Defining a coupel of new validators
# LengthValidator ensures that length of text equals difference between offsets
class LengthValidator(Validator):
    """ Validates that a text field's length is correct based on other fields """

    def __init__(self, **kwargs):
        super(LengthValidator, self).__init__(**kwargs)
        self.failures = set([])

    def validate(self, field, row={}):
        expectedLen = int(row["endOffset"]) - int(row["startOffset"])
        textLen = len(field)
        if expectedLen != textLen and (field or not self.empty_ok):
            self.failures.add(field)
            raise ValidationException(
                "'{}' length {} does not match extpected length /{}/".format(field, textLen, expectedLen)
            )

    @property
    def bad(self):
        return self.failures

# JsonValidator has multiple checks
# - text is valid json
# - dictionary keys all valid choices
# - further validating dictionary keys by annotType
# - mods field value is a list
# - mods list entries are all valid

class JsonValidator(Validator):
    """ Validates that a field contains valid JSON data """

    def __init__(self, **kwargs):
        super(JsonValidator, self).__init__(**kwargs)
        self.failures = set([])

    def validate(self, field, row={}):
        if ((not field) and (not self.empty_ok)):
            self.failures.add(field)
            raise ValidationException(
                "'{}' is empty".format(field)
            )
        elif (field != ""):
            #print(field)
            try:
                data = json.loads(field)
                #print(list(data.keys()))
                if not all(k in ["HasQuantity", "HasProperty", "Qualifies", "mods", "unit"]
                           for k in list(data.keys())):
                    self.failures.add(field)
                    raise ValidationException(
                        "'{}' has invalid key".format(field)
                    )
                if row["annotType"] == "Quantity":
                    if not all(k in ["mods", "unit"] for k in list(data.keys())):
                        self.failures.add(field)
                        raise ValidationException(
                            "'{}' has invalid key".format(field)
                        )
                    if "mods" in list(data.keys()):
                        if type(data["mods"]) != list:
                            self.failures.add(field)
                            raise ValidationException(
                                "'{}' mods field is not a list".format(field)
                            )
                        if not all(k in ['IsCount', 'IsApproximate', 'IsMeanHasTolerance', 'IsMedian',
                                          'IsList', 'IsRangeHasTolerance', 'IsMean', 'IsRange',
                                          'HasTolerance', 'IsMeanIsRange', 'IsMeanHasSD'] for k in data["mods"]):
                            self.failures.add(field)
                            raise ValidationException(
                                "'{}' has invalid key in mods".format(field)
                            )
                if row["annotType"] == "MeasuredEntity":
                    if not all(k in ["HasProperty", "HasQuantity"] for k in list(data.keys())):
                        self.failures.add(field)
                        raise ValidationException(
                            "'{}' has invalid key".format(field)
                        )
                if row["annotType"] == "MeasuredProperty":
                    if not all(k in ["HasQuantity"] for k in list(data.keys())):
                        self.failures.add(field)
                        raise ValidationException(
                            "'{}' has invalid key".format(field)
                        )
                if row["annotType"] == "Qualifier":
                    if not all(k in ["Qualifies"] for k in list(data.keys())):
                        self.failures.add(field)
                        raise ValidationException(
                            "'{}' has invalid key".format(field)
                        )
            except json.decoder.JSONDecodeError:
                self.failures.add(field)
                raise ValidationException(
                    "'{}' is not valid json".format(field)
                )

    @property
    def bad(self):
        return self.failures

# We could do the validation directioly in the first read block below
# But for now let's add an additional read through the data
# Annoyingly, you have to instantiate that "validators" block for each record
# Otherwise they seem to accumulate extra values.

# Beyond the 2 new validators defined above, we're using vladiate defined options

allgood=True
badfiles = []
for sfn in os.listdir(args.indir+args.sub):
    if sfn not in skip and sfn.endswith(".tsv"):
        validators = {
            'docId': [
                UniqueValidator(unique_with=['annotSet', 'annotId']),
                #UniqueValidator(unique_with=['annotSet', 'startOffset'])
            ],
            'annotId': [
                RegexValidator(pattern=r'T?\d*-?\d+', full=True)
            ],
            'annotType': [
                SetValidator(['Quantity', 'Qualifier', 'MeasuredProperty', 'MeasuredEntity'])
            ],
            'annotSet': [
                IntValidator()
            ],
            'startOffset': [
                IntValidator()
            ],
            'endOffset': [
                IntValidator()
            ],
            'other': [
                JsonValidator(empty_ok=True)
            ],
            'text': [
                LengthValidator()
            ]
        }
        #print(sfn)
        truth = Vlad(source=LocalFile(args.indir+args.sub+sfn), validators=validators, delimiter="\t").validate()
        if truth == False:
            allgood=False
            badfiles.append(sfn)

# If any tsv files fail validation, report list to user and exit program
if allgood == False:
    print("You have invalid tsv data in your submission")
    print("Invalid files: " +str(badfiles))
    print("Scroll up to see specific problems.")
    print("For more detailed errors, enable debug level logging.")
    exit()

if args.val == True:
    print("Running in validate only mode.")
    print("Validation finished.")
    print("Have a nice day!")
    exit()

# Once we've validated all submission data, we start building our eval data
# Everything is going to be done in Pandas
# Since Pandas doesn't natively support theta joins, we will be using the
# pandasql library to handle all our joins and checks.
for sfn in os.listdir(args.indir+args.sub):
    if sfn not in skip and sfn.endswith(".tsv"):
        subnames.append(sfn)
        subdfs.append(pd.read_csv(args.indir+args.sub+sfn, sep="\t"))
for gfn in os.listdir(args.indir+args.gold):
    # Currently, we are only checking against files present in the submission
    # This is so that users can evaluate whatever portion of the data
    # they chose to keep separate from their training data.
    # This filter is not in place in the codalab copy of this code.
    if args.limit == True:
        if gfn in subnames:
            goldfs.append(pd.read_csv(args.indir+args.gold+gfn, sep="\t"))
    else:
        goldfs.append(pd.read_csv(args.indir+args.gold+gfn, sep="\t"))


print("Submission directory contains: " + str(len(subdfs)))
print("Gold directory contains: " + str(len(goldfs)))

gold = pd.concat(goldfs, ignore_index=True)
sub = pd.concat(subdfs, ignore_index=True)

# Forcing some types, and also setting empty fields for later lambdas
gold['annotSet'].astype('int')
gold['startOffset'].astype('int')
gold['endOffset'].astype('int')
sub['annotSet'].astype('int')
sub['startOffset'].astype('int')
sub['endOffset'].astype('int')
gold['EM'] = None
gold['F1'] = None
gold['maxF1'] = None
sub['EM'] = None
sub['F1'] = None
sub['maxF1'] = None


#print(gold.shape)
#print(sub.shape)

# Report annotation type counts for both Gold and Submission
for annotType in ["Quantity", "MeasuredProperty", "MeasuredEntity", "Qualifier"]:
    print("Gold count of " + annotType + ": " + str(len(gold.loc[gold["annotType"] == annotType].index)))
print("")
for annotType in ["Quantity", "MeasuredProperty", "MeasuredEntity", "Qualifier"]:
    print("Submission count of " + annotType + ": " + str(len(sub.loc[sub["annotType"] == annotType].index)))
print("")

# Start processing quantities

goldQuants = gold.loc[gold["annotType"] == "Quantity"]
subQuants = sub.loc[sub["annotType"] == "Quantity"]

# pandasql to get our matches for Quantity

# Note that we are processing everything keyed on Quantities.
# Any even partially matched quantity will be marked as a match
# All other components will only be credited if associated with a matching quantity
# So if a submission has identified the MeasuredEntity spans correctly
# but has them assocaited with incorrectly matched quantities
# credit will not be given for those matches.

# Matching here is done in two steps.
# matching quantities in the submission file are given appropriate
# annotSet and annotId values drawn from teh matching gold data

q = """SELECT
        s.annotSet, g.annotSet as gAnnotSet, s.docId, s.annotType, s.annotId,
        g.annotId as gAnnotId, s.startOffset, s.endOffset, s.text, s.EM, s.F1, s.maxF1
     FROM
        subQuants s
     JOIN
        goldQuants g
           ON (s.docId = g.docId
           AND ((s.startOffset >= g.startOffset AND s.startOffset <= g.endOffset)
           OR (s.endOffset >= g.startOffset AND s.endOffset <= g.endOffset)
           OR (g.startOffset >= s.startOffset AND g.startOffset <= s.endOffset)
           OR (g.endOffset >= s.startOffset AND g.endOffset <= s.endOffset)))"""

subMatches = pysqldf(q)

# Those matching annotSet and annotId values are then used to build a joined dataset

q = """SELECT
        l.annotSet, l.gAnnotSet, l.docId, l.annotId, l.gAnnotId,
        l.startOffset as aStart, l.endOffset as aEnd, l.text as aText,
        r.startOffset as gStart, r.endOffset as gEnd, r.text as gText,
        l.EM, l.F1, l.maxF1
     FROM
        subMatches l
     JOIN
        goldQuants r
           ON (l.docId = r.docId
           AND l.gAnnotSet = r.annotSet
           AND l.gAnnotId = r.annotId)"""

quantityMatches = pysqldf(q)

# within the joined dataset, exact match (EM) scores are assigned if the
# submission and gold start and end offsets align exactly.

quantityMatches['EM'] = quantityMatches.apply (lambda x: 1.0 if (x.aStart == x.gStart and x.aEnd == x.gEnd) else 0, axis = 1 )

# For a SQuAD-style "F1" overlap score
# we calculate token level overlap between the submission and gold endpoints
# Tokenization is done using a simple space delimited method
def calcF1 (row):
    aTokensSize = len(row.aText.split(" "))
    gTokensSize = len(row.gText.split(" "))
    overlapStart = max(row.aStart, row.gStart)
    overlapEnd = min(row.aEnd, row.gEnd)
    overlapSubStrStart = 0
    overlapSubStrLen = int(overlapEnd - overlapStart)
    if (overlapStart > row.aStart):
        overlapSubStrStart = int(overlapStart - row.aStart)
    overlapText = row.aText[overlapSubStrStart:overlapSubStrStart+overlapSubStrLen]
    overlapTokenCnt = len(overlapText.split(" "))

    precision = 1.0 * overlapTokenCnt / aTokensSize
    recall = 1.0 * overlapTokenCnt / gTokensSize

    F1 = (2 * precision * recall) / (precision + recall)
    return F1

quantityMatches['F1'] = quantityMatches.apply (lambda x: calcF1(x), axis = 1 )

# If there are multiple matches, we will give the highest F1 score.
quantityMatches['maxF1'] = quantityMatches.groupby(['docId', 'annotId'])['F1'].transform('max')

# We also create sets of submission only Quantities and Gold Only Quantiites
# These will be scored as both EM and F1 = 0 in the final evaluation,
# so that precision and recall impact the overall score.

q = """SELECT
        l.*
     FROM
        subQuants l
     LEFT JOIN
        quantityMatches r
           ON (l.docId = r.docId
           AND l.annotSet = r.annotSet
           AND l.annotId = r.annotId)
           WHERE r.docId is NULL"""

subOnlyQuants = pysqldf(q)

q = """SELECT
        l.*
     FROM
        goldQuants l
     LEFT JOIN
        quantityMatches r
           ON (l.docId = r.docId
           AND l.annotSet = r.gAnnotSet
           AND l.annotId = r.gAnnotId)
           WHERE r.docId is NULL"""

goldOnlyQuants = pysqldf(q)

# Next, we collect those alignments from the quantity matches
# And propagate them through the rest of the submission.

# Note that (TODO) more than 1 submission value can match the same gold data
# and those scores will be duplicated. However, beyond the Quantity scores, only 1 gold datapoint
# can match a given submission annotSet. (TODO: Confirm that I'm not describing this backward.)

annotSetAlignments = quantityMatches[["docId", "annotSet", "gAnnotSet"]].rename(columns={"gAnnotSet":"matchAnnotSet"})
annotSetAlignmentsDict = annotSetAlignments.groupby('docId').apply(lambda x: dict(zip(x.annotSet, x.matchAnnotSet))).to_dict()

# Update submission data with corresponding gold annotSet if applicable.
sub["gAnnotSet"] = None
sub["gAnnotSet"] = sub.apply(lambda x: annotSetAlignmentsDict[x.docId][x.annotSet] if x.docId in annotSetAlignmentsDict and x.annotSet in annotSetAlignmentsDict[x.docId] else None , axis = 1)

# Now we'll process our units
# This requires unpacking that data from the json in the other column

goldUnits = goldQuants[goldQuants.other.notnull()].copy()
goldUnits['json'] = goldUnits.apply (lambda x: json.loads(str(x.other)) if str(x.other) != "nan" else "", axis = 1 )
goldUnits['unit'] = goldUnits.apply (lambda x: x.json["unit"] if "unit" in x.json.keys() else "", axis = 1)
goldUnits = goldUnits[goldUnits.unit != ""][["docId", "annotSet", "annotType", "startOffset", "endOffset", "annotId", "text", "unit"]]

#print(sub[sub["annotType"] == "Quantity"].head())

subUnits = sub[sub["annotType"] == "Quantity"].copy() #and sub.other.notnull()]
subUnits = subUnits[subUnits.other.notnull()]
subUnits['json'] = None
subUnits['unit'] = None
subUnits['json'] = subUnits.apply (lambda x: json.loads(str(x.other)) if str(x.other) != "nan" else "", axis = 1 )
subUnits['unit'] = subUnits.apply (lambda x: x.json["unit"] if "unit" in x.json.keys() else "", axis = 1)
#print(subUnits.head())
subUnits = subUnits[subUnits.unit != ""][["docId", "annotSet", "gAnnotSet", "annotType", "startOffset", "endOffset", "annotId", "text", "unit", "EM", "F1", "maxF1"]]

# We'll now use our same matching strategy to score units,
# ensuring that the text of the unit matches.
q = """SELECT
       s.gAnnotSet as matchAnnotSet, g.annotSet as gAnnotSet, s.docId, s.annotType, s.annotId,
       g.annotId as gAnnotId, s.startOffset, s.endOffset, s.text as sText, g.text as gText,
       s.unit as sUnit, g.unit as gUnit, s.EM, s.F1, s.maxF1
     FROM
        subUnits s
     JOIN
        goldUnits g
           ON (s.docId = g.docId
           AND s.gAnnotSet = g.annotSet
           AND s.unit = g.unit)"""
unitMatches = pysqldf(q)

# EM and F1 here are both binary (no partial overlap matches)
unitMatches['EM'] = unitMatches.apply (lambda x: 1.0 if (x.sUnit == x.gUnit) else 0, axis = 1 )
unitMatches['F1'] = unitMatches.apply (lambda x: 1.0 if (x.sUnit == x.gUnit) else 0, axis = 1 )

# And again, submission only and gold set only units
q = """SELECT
        l.*
     FROM
        subUnits l
     LEFT JOIN
        unitMatches r
           ON (l.docId = r.docId
           AND l.gAnnotSet = r.gAnnotSet)
           WHERE r.docId is NULL"""
subOnlyUnits = pysqldf(q)

q = """SELECT
        l.*
     FROM
        goldUnits l
     LEFT JOIN
        unitMatches r
           ON (l.docId = r.docId
           AND l.annotSet = r.gAnnotSet)
           WHERE r.docId is NULL"""
goldOnlyUnits = pysqldf(q)

# We do the same routine for MeasuredEntities
goldEntities = gold.loc[gold["annotType"] == "MeasuredEntity"]
subEntities = sub.loc[sub["annotType"] == "MeasuredEntity"]

q = """SELECT
        s.annotSet, s.gAnnotSet, s.docId, s.annotType, s.annotId,
        g.annotId as gAnnotId, s.startOffset as aStart, g.startOffset as gStart,
        s.endOffset as aEnd, g.endOffset as gEnd, s.text as aText, g.text as gText, s.other,
        s.EM, s.F1, s.maxF1
     FROM
        subEntities s
     JOIN
        goldEntities g
           ON (s.docId = g.docId
           AND s.gAnnotSet = g.annotSet
           AND ((s.startOffset >= g.startOffset AND s.startOffset <= g.endOffset)
           OR (s.endOffset >= g.startOffset AND s.endOffset <= g.endOffset)
           OR (g.startOffset >= s.startOffset AND g.startOffset <= s.endOffset)
           OR (g.endOffset >= s.startOffset AND g.endOffset <= s.endOffset)))"""

entityMatches = pysqldf(q)

entityMatches['EM'] = entityMatches.apply (lambda x: 1.0 if (x.aStart == x.gStart and x.aEnd == x.gEnd) else 0, axis = 1 )
entityMatches['F1'] = entityMatches.apply (lambda x: calcF1(x), axis = 1 )
entityMatches['maxF1'] = entityMatches.groupby(['docId', 'annotId'])['F1'].transform('max')

q = """SELECT
        l.*
     FROM
        subEntities l
     LEFT JOIN
        entityMatches r
           ON (l.docId = r.docId
           AND l.annotSet = r.annotSet
           AND l.annotId = r.annotId)
           WHERE r.docId is NULL"""
subOnlyEntities = pysqldf(q)

q = """SELECT
        l.*
     FROM
        goldEntities l
     LEFT JOIN
        entityMatches r
           ON (l.docId = r.docId
           AND l.annotSet = r.gAnnotSet
           AND l.annotId = r.gAnnotId)
           WHERE r.docId is NULL"""
goldOnlyEntities = pysqldf(q)

# We do the same routine for MeasuredProperties
goldProperties = gold.loc[gold["annotType"] == "MeasuredProperty"]
subProperties = sub.loc[sub["annotType"] == "MeasuredProperty"]

q = """SELECT
        s.annotSet, s.gAnnotSet, s.docId, s.annotType, s.annotId,
        g.annotId as gAnnotId, s.startOffset as aStart, g.startOffset as gStart,
        s.endOffset as aEnd, g.endOffset as gEnd, s.text as aText, g.text as gText, s.other,
        s.EM, s.F1, s.maxF1
     FROM
        subProperties s
     JOIN
        goldProperties g
           ON (s.docId = g.docId
           AND s.gAnnotSet = g.annotSet
           AND ((s.startOffset >= g.startOffset AND s.startOffset <= g.endOffset)
           OR (s.endOffset >= g.startOffset AND s.endOffset <= g.endOffset)
           OR (g.startOffset >= s.startOffset AND g.startOffset <= s.endOffset)
           OR (g.endOffset >= s.startOffset AND g.endOffset <= s.endOffset)))"""
propertyMatches = pysqldf(q)
propertyMatches['EM'] = propertyMatches.apply (lambda x: 1.0 if (x.aStart == x.gStart and x.aEnd == x.gEnd) else 0, axis = 1 )
propertyMatches['F1'] = propertyMatches.apply (lambda x: calcF1(x), axis = 1 )
propertyMatches['maxF1'] = propertyMatches.groupby(['docId', 'annotId'])['F1'].transform('max')

q = """SELECT
        l.*
     FROM
        subProperties l
     LEFT JOIN
        propertyMatches r
           ON (l.docId = r.docId
           AND l.annotSet = r.annotSet
           AND l.annotId = r.annotId)
           WHERE r.docId is NULL"""
subOnlyProperties = pysqldf(q)

q = """SELECT
        l.*
     FROM
        goldProperties l
     LEFT JOIN
        propertyMatches r
           ON (l.docId = r.docId
           AND l.annotSet = r.gAnnotSet
           AND l.annotId = r.gAnnotId)
           WHERE r.docId is NULL"""
goldOnlyProperties = pysqldf(q)

# We do the same routine for Qualifiers:
goldQualifiers = gold.loc[gold["annotType"] == "Qualifier"]
subQualifiers = sub.loc[sub["annotType"] == "Qualifier"]

q = """SELECT
        s.annotSet, s.gAnnotSet, s.docId, s.annotType, s.annotId,
        g.annotId as gAnnotId, s.startOffset as aStart, g.startOffset as gStart,
        s.endOffset as aEnd, g.endOffset as gEnd, s.text as aText, g.text as gText, s.other,
        s.EM, s.F1, s.maxF1
     FROM
        subQualifiers s
     JOIN
        goldQualifiers g
           ON (s.docId = g.docId
           AND s.gAnnotSet = g.annotSet
           AND ((s.startOffset >= g.startOffset AND s.startOffset <= g.endOffset)
           OR (s.endOffset >= g.startOffset AND s.endOffset <= g.endOffset)
           OR (g.startOffset >= s.startOffset AND g.startOffset <= s.endOffset)
           OR (g.endOffset >= s.startOffset AND g.endOffset <= s.endOffset)))"""
qualifierMatches = pysqldf(q)
# qualifierMatches['EM'] = None
# qualifierMatches['F1'] = None
# qualifierMatches['maxF1'] = None

qualifierMatches['EM'] = qualifierMatches.apply (lambda x: 1.0 if (x.aStart == x.gStart and x.aEnd == x.gEnd) else 0, axis = 1 )
qualifierMatches['F1'] = qualifierMatches.apply (lambda x: calcF1(x), axis = 1 )
qualifierMatches['maxF1'] = qualifierMatches.groupby(['docId', 'annotId'])['F1'].transform('max')


q = """SELECT
        l.*
     FROM
        subQualifiers l
     LEFT JOIN
        qualifierMatches r
           ON (l.docId = r.docId
           AND l.annotSet = r.annotSet
           AND l.annotId = r.annotId)
           WHERE r.docId is NULL"""
subOnlyQualifiers = pysqldf(q)

q = """SELECT
        l.*
     FROM
        goldQualifiers l
     LEFT JOIN
        qualifierMatches r
           ON (l.docId = r.docId
           AND l.annotSet = r.gAnnotSet
           AND l.annotId = r.gAnnotId)
           WHERE r.docId is NULL"""
goldOnlyQualifiers = pysqldf(q)

# Now we will process and score all relationships.
# Relations are drawn from the "Other" column of the TSV data
# The "source" of a relationship is the annotation associated with a given row.
# It's relation type and "target" are established in the "other" field
# Our validation ensures that any annotation of type MeasuredEntity, MeasuredProperty, or Qualifier
# Includes the appropriate relationship.

# We pull the relationships out of the json data:
tmpgRels = gold.loc[gold["annotType"].isin(["MeasuredEntity", "MeasuredProperty", "Qualifier"])].copy()
tmpgRels['json'] = tmpgRels.apply (lambda x: json.loads(str(x.other)) if str(x.other) != "nan" else "", axis = 1 )
tmpgRels['relType'] = tmpgRels.apply (lambda x: list(x.json.keys())[0], axis = 1 )
tmpgRels['target'] = tmpgRels.apply (lambda x: list(x.json.values())[0], axis = 1 )
tmpgRels['src'] = tmpgRels.apply (lambda x: x.annotId, axis=1)

goldRels = tmpgRels[["docId", "annotSet", "annotType", "relType", "src", "target"]]

tmpsRels = sub.loc[sub["annotType"].isin(["MeasuredEntity", "MeasuredProperty", "Qualifier"])].copy()
tmpsRels['json'] = None
tmpsRels['relType'] = None
tmpsRels['target'] = None
tmpsRels['src'] = None
tmpsRels['json'] = tmpsRels.apply (lambda x: json.loads(str(x.other)) if str(x.other) != "nan" else "", axis = 1 )
tmpsRels['relType'] = tmpsRels.apply (lambda x: list(x.json.keys())[0], axis = 1 )
tmpsRels['target'] = tmpsRels.apply (lambda x: list(x.json.values())[0], axis = 1 )
tmpsRels['src'] = tmpsRels.apply (lambda x: x.annotId, axis=1)

subRels = tmpsRels[["docId", "annotSet", "gAnnotSet", "annotType", "relType", "src", "target"]]

# We process "HasQuantity"
# Source should be either a MeasuredEntity or a MeasuredProperty
# Target should be a Quantity
# We will build out the submission relationships
# Grab the corresponding gold set ids for any matching source and target endpoint
# And score accordingly.

goldHasQuant = goldRels.loc[goldRels["relType"] == "HasQuantity"]
subHasQuant = subRels.loc[subRels["relType"] == "HasQuantity"]

eqMatch = pd.concat([entityMatches, propertyMatches], ignore_index=True)

q = """SELECT
        l.*, r.gAnnotId as gSrc
     FROM
        subHasQuant l
     LEFT JOIN
        eqMatch r
           ON (l.docId = r.docId
           AND l.annotSet = r.annotSet
           AND l.src = r.annotId)
           WHERE r.docId not NULL"""
subHasQuant1 = pysqldf(q)

q = """SELECT
        l.*, r.gAnnotId as gTarget
     FROM
        subHasQuant1 l
     LEFT JOIN
        quantityMatches r
           ON (l.docId = r.docId
           AND l.annotSet = r.annotSet
           AND l.target = r.annotId)
           WHERE r.docId not NULL"""
subHasQuant2 = pysqldf(q)

q = """SELECT
        s.*
     FROM
        subHasQuant2 s
     JOIN
        goldHasQuant g
           ON (s.docId = g.docId
           AND s.gAnnotSet = g.annotSet
           AND s.relType = g.relType
           AND s.gSrc = g.src
           AND s.gTarget = g.target)"""

# EM and F1 here are both binary (no partial overlap matches)
hasQuantMatch = pysqldf(q)
hasQuantMatch['EM'] = hasQuantMatch.apply (lambda x: 1.0, axis = 1 )
hasQuantMatch['F1'] = None
#print(hasQuantMatch)

# As usual, we have both our Gold only and Submission only data.
q = """SELECT
        l.*
     FROM
        subHasQuant l
     LEFT JOIN
        hasQuantMatch r
           ON (l.docId = r.docId
           AND l.annotSet = r.AnnotSet
           AND l.src = r.src)
           WHERE r.docId is NULL"""
subOnlyHasQuant = pysqldf(q)

q = """SELECT
        l.*
     FROM
        goldHasQuant l
     LEFT JOIN
        hasQuantMatch r
           ON (l.docId = r.docId
           AND l.annotSet = r.gAnnotSet
           AND l.src = r.gSrc)
           WHERE r.docId is NULL"""
goldOnlyHasQuant = pysqldf(q)

# Do the same thing for HasProperty
# Has property is always from a MeasuredEntity to a MeasuredProperty
goldHasProp = goldRels.loc[goldRels["relType"] == "HasProperty"]
subHasProp = subRels.loc[subRels["relType"] == "HasProperty"]

q = """SELECT
        l.*, r.gAnnotId as gSrc
     FROM
        subHasProp l
     LEFT JOIN
        entityMatches r
           ON (l.docId = r.docId
           AND l.annotSet = r.annotSet
           AND l.src = r.annotId)
           WHERE r.docId not NULL"""
subHasProp1 = pysqldf(q)

q = """SELECT
        l.*, r.gAnnotId as gTarget
     FROM
        subHasProp1 l
     LEFT JOIN
        propertyMatches r
           ON (l.docId = r.docId
           AND l.annotSet = r.annotSet
           AND l.target = r.annotId)
           WHERE r.docId not NULL"""
subHasProp2 = pysqldf(q)

q = """SELECT
        s.*
     FROM
        subHasProp2 s
     JOIN
        goldHasProp g
           ON (s.docId = g.docId
           AND s.gAnnotSet = g.annotSet
           AND s.relType = g.relType
           AND s.gSrc = g.src
           AND s.gTarget = g.target)"""
hasPropMatch = pysqldf(q)

# EM and F1 here are both binary (no partial overlap matches)
hasPropMatch['EM'] = hasPropMatch.apply (lambda x: 1.0, axis = 1 )
hasPropMatch['F1'] = None

# As usual, we have both our Gold only and Submission only data.
q = """SELECT
        l.*
     FROM
        subHasProp l
     LEFT JOIN
        hasPropMatch r
           ON (l.docId = r.docId
           AND l.annotSet = r.AnnotSet
           AND l.src = r.src)
           WHERE r.docId is NULL"""
subOnlyHasProp = pysqldf(q)

q = """SELECT
        l.*
     FROM
        goldHasProp l
     LEFT JOIN
        hasPropMatch r
           ON (l.docId = r.docId
           AND l.annotSet = r.gAnnotSet
           AND l.src = r.gSrc)
           WHERE r.docId is NULL"""
goldOnlyHasProp = pysqldf(q)

# Processing "Qualifies" Relationships
# Source here is always a Qualifier
# Target can be  any of entities, properties, or quantities
# TODO: We had some cases where a qualifier could qualify a qualifier
# Make sure these are gone. :)

goldQualifies = goldRels.loc[goldRels["relType"] == "Qualifies"]
subQualifies = subRels.loc[subRels["relType"] == "Qualifies"]

destMatch = pd.concat([entityMatches, propertyMatches, quantityMatches], ignore_index=True)

q = """SELECT
        l.*, r.gAnnotId as gSrc, r.EM, r.F1, r.maxF1
     FROM
        subQualifies l
     LEFT JOIN
        qualifierMatches r
           ON (l.docId = r.docId
           AND l.annotSet = r.annotSet
           AND l.src = r.annotId)
           WHERE r.docId not NULL"""
subQualifies1 = pysqldf(q)

q = """SELECT
        l.*, r.gAnnotId as gTarget
     FROM
        subQualifies1 l
     LEFT JOIN
        destMatch r
           ON (l.docId = r.docId
           AND l.annotSet = r.annotSet
           AND l.target = r.annotId)
           WHERE r.docId not NULL"""
subQualifies2 = pysqldf(q)

q = """SELECT
        s.*
     FROM
        subQualifies2 s
     JOIN
        goldQualifies g
           ON (s.docId = g.docId
           AND s.gAnnotSet = g.annotSet
           AND s.relType = g.relType
           AND s.gSrc = g.src
           AND s.gTarget = g.target)"""
qualifiesMatch = pysqldf(q)

# EM and F1 here are both binary (no partial overlap matches)
qualifiesMatch['EM'] = qualifiesMatch.apply (lambda x: 1.0, axis = 1 )
qualifiesMatch['F1'] = None

# As usual, we have both our Gold only and Submission only data.
q = """SELECT
        l.*
     FROM
        subQualifies l
     LEFT JOIN
        qualifiesMatch r
           ON (l.docId = r.docId
           AND l.annotSet = r.AnnotSet
           AND l.src = r.src)
           WHERE r.docId is NULL"""
subOnlyQualifies = pysqldf(q)

q = """SELECT
        l.*
     FROM
        goldQualifies l
     LEFT JOIN
        qualifiesMatch r
           ON (l.docId = r.docId
           AND l.annotSet = r.gAnnotSet
           AND l.src = r.gSrc)
           WHERE r.docId is NULL"""
goldOnlyQualifies = pysqldf(q)

# Final component are our modifiers.
# there can be more than one modifier per Quantity
# Note the use of "explodes" -- this requires Pandas >= 1.0.
# Pulling these again out of the "other" data, and processing the json
goldMods = goldQuants[goldQuants.other.notnull()].copy()
goldMods['json'] = goldMods.apply (lambda x: json.loads(str(x.other)) if str(x.other) != "nan" else "", axis = 1 )
goldMods['mods'] = goldMods.apply (lambda x: x.json["mods"] if "mods" in x.json.keys() else "", axis = 1)

goldMods = goldMods.explode('mods')
goldMods = goldMods[goldMods.mods != ""][["docId", "annotSet", "annotType", "startOffset", "endOffset", "annotId", "text", "mods", "EM", "F1", "maxF1"]]

subMods = sub[sub["annotType"] == "Quantity"].copy()
subMods = subMods[subMods.other.notnull()]
subMods['json'] = None
subMods['mods'] = None
subMods['json'] = subMods.apply (lambda x: json.loads(str(x.other)) if str(x.other) != "nan" else "", axis = 1 )
subMods['mods'] = subMods.apply (lambda x: x.json["mods"] if "mods" in x.json.keys() else "", axis = 1)

subMods = subMods.explode('mods')
subMods = subMods[subMods.mods != ""][["docId", "annotSet", "gAnnotSet", "annotType", "startOffset", "endOffset", "annotId", "text", "mods", "EM", "F1", "maxF1"]]

q = """SELECT
       s.gAnnotSet as matchAnnotSet, g.annotSet as gAnnotSet, s.docId, s.annotType, s.annotId,
       g.annotId as gAnnotId, s.startOffset, s.endOffset, s.text as sText, g.text as gText,
       s.mods as sMods, g.mods as gMods, s.EM, s.F1, s.maxF1
     FROM
        subMods s
     JOIN
        goldMods g
           ON (s.docId = g.docId
           AND s.gAnnotSet = g.annotSet
           AND s.mods = g.mods)"""
modsMatches = pysqldf(q)
modsMatches['EM'] = modsMatches.apply (lambda x: 1.0, axis = 1 )

q = """SELECT
        l.*
     FROM
        subMods l
     LEFT JOIN
        modsMatches r
           ON (l.docId = r.docId
           AND l.gAnnotSet = r.gAnnotSet
           AND l.mods = r.sMods)
           WHERE r.docId is NULL"""
subOnlyMods = pysqldf(q)

q = """SELECT
        l.*
     FROM
        goldMods l
     LEFT JOIN
        modsMatches r
           ON (l.docId = r.docId
           AND l.annotSet = r.gAnnotSet
           AND l.mods = r.gMods)
           WHERE r.docId is NULL"""
goldOnlyMods = pysqldf(q)

# Penalty is defined as 0
# This is where we apply the EM and F1 of 0 to all of our sub only and gold only results
# We will also apply annotTypes to data that don't already have one
# And we'll apply match types of "Match", "Gold only", and Sub only
# These match types allow us to calculate precision, recall, and F1 for teh overall scores
# Or at the document level or "class" level per the mode the evaluation script runs in.

penalty = 0

quantityMatches["annotType"] = quantityMatches.apply (lambda x: "Quantity", axis = 1 )
quantityMatches["matchType"] = quantityMatches.apply (lambda x: "Match", axis = 1 )
subOnlyQuants["EM"] = subOnlyQuants.apply (lambda x: penalty, axis = 1)
subOnlyQuants["F1"] = subOnlyQuants.apply (lambda x: penalty, axis = 1)
subOnlyQuants["matchType"] = subOnlyQuants.apply (lambda x: "Sub only", axis = 1 )
goldOnlyQuants["EM"] = goldOnlyQuants.apply (lambda x: penalty, axis = 1)
goldOnlyQuants["F1"] = goldOnlyQuants.apply (lambda x: penalty, axis = 1)
goldOnlyQuants["matchType"] = goldOnlyQuants.apply (lambda x: "Gold only", axis = 1 )

unitMatches["annotType"] = unitMatches.apply (lambda x: "Unit", axis = 1)
unitMatches["matchType"] = unitMatches.apply (lambda x: "Match", axis = 1 )
subOnlyUnits["annotType"] = subOnlyUnits.apply (lambda x: "Unit", axis = 1)
goldOnlyUnits["annotType"] = goldOnlyUnits.apply (lambda x: "Unit", axis = 1)
subOnlyUnits["EM"] = subOnlyUnits.apply (lambda x: penalty, axis = 1)
subOnlyUnits["F1"] = subOnlyUnits.apply (lambda x: penalty, axis = 1)
subOnlyUnits["matchType"] = subOnlyUnits.apply (lambda x: "Sub only", axis = 1 )
goldOnlyUnits["EM"] = goldOnlyUnits.apply (lambda x: penalty, axis = 1)
goldOnlyUnits["F1"] = goldOnlyUnits.apply (lambda x: penalty, axis = 1)
goldOnlyUnits["matchType"] = goldOnlyUnits.apply (lambda x: "Gold only", axis = 1 )
goldOnlyUnits["matchType"] = goldOnlyUnits.apply (lambda x: "Gold only", axis = 1 )

entityMatches["matchType"] = entityMatches.apply (lambda x: "Match", axis = 1 )
subOnlyEntities["EM"] = subOnlyEntities.apply (lambda x: penalty, axis = 1)
subOnlyEntities["F1"] = subOnlyEntities.apply (lambda x: penalty, axis = 1)
subOnlyEntities["matchType"] = subOnlyEntities.apply (lambda x: "Sub only", axis = 1 )
goldOnlyEntities["EM"] = goldOnlyEntities.apply (lambda x: penalty, axis = 1)
goldOnlyEntities["F1"] = goldOnlyEntities.apply (lambda x: penalty, axis = 1)
goldOnlyEntities["matchType"] = goldOnlyEntities.apply (lambda x: "Gold only", axis = 1 )

propertyMatches["matchType"] = propertyMatches.apply (lambda x: "Match", axis = 1 )
subOnlyProperties["EM"] = subOnlyProperties.apply (lambda x: penalty, axis = 1)
subOnlyProperties["F1"] = subOnlyProperties.apply (lambda x: penalty, axis = 1)
subOnlyProperties["matchType"] = subOnlyProperties.apply (lambda x: "Sub only", axis = 1 )
goldOnlyProperties["EM"] = goldOnlyProperties.apply (lambda x: penalty, axis = 1)
goldOnlyProperties["F1"] = goldOnlyProperties.apply (lambda x: penalty, axis = 1)
goldOnlyProperties["matchType"] = goldOnlyProperties.apply (lambda x: "Gold only", axis = 1 )

qualifierMatches["matchType"] = qualifierMatches.apply (lambda x: "Match", axis = 1 )
subOnlyQualifiers["EM"] = subOnlyQualifiers.apply (lambda x: penalty, axis = 1)
subOnlyQualifiers["F1"] = subOnlyQualifiers.apply (lambda x: penalty, axis = 1)
subOnlyQualifiers["matchType"] = subOnlyQualifiers.apply (lambda x: "Sub only", axis = 1 )
goldOnlyQualifiers["EM"] = goldOnlyQualifiers.apply (lambda x: penalty, axis = 1)
goldOnlyQualifiers["F1"] = goldOnlyQualifiers.apply (lambda x: penalty, axis = 1)
goldOnlyQualifiers["matchType"] = goldOnlyQualifiers.apply (lambda x: "Gold only", axis = 1 )

hasQuantMatch["matchType"] = hasQuantMatch.apply (lambda x: "Match", axis = 1 )
hasQuantMatch["F1"] = hasQuantMatch.apply (lambda x: x.EM, axis = 1)
subOnlyHasQuant["EM"] = subOnlyHasQuant.apply (lambda x: penalty, axis = 1)
subOnlyHasQuant["F1"] = subOnlyHasQuant.apply (lambda x: penalty, axis = 1)
subOnlyHasQuant["matchType"] = subOnlyHasQuant.apply (lambda x: "Sub only", axis = 1 )
goldOnlyHasQuant["EM"] = goldOnlyHasQuant.apply (lambda x: penalty, axis = 1)
goldOnlyHasQuant["F1"] = goldOnlyHasQuant.apply (lambda x: penalty, axis = 1)
goldOnlyHasQuant["matchType"] = goldOnlyHasQuant.apply (lambda x: "Gold only", axis = 1 )

hasPropMatch["matchType"] = hasPropMatch.apply (lambda x: "Match", axis = 1 )
hasPropMatch["F1"] = hasPropMatch.apply (lambda x: x.EM, axis = 1)
subOnlyHasProp["EM"] = subOnlyHasProp.apply (lambda x: penalty, axis = 1)
subOnlyHasProp["F1"] = subOnlyHasProp.apply (lambda x: penalty, axis = 1)
subOnlyHasProp["matchType"] = subOnlyHasProp.apply (lambda x: "Sub only", axis = 1 )
goldOnlyHasProp["EM"] = goldOnlyHasProp.apply (lambda x: penalty, axis = 1)
goldOnlyHasProp["F1"] = goldOnlyHasProp.apply (lambda x: penalty, axis = 1)
goldOnlyHasProp["matchType"] = goldOnlyHasProp.apply (lambda x: "Gold only", axis = 1 )

qualifiesMatch["matchType"] = qualifiesMatch.apply (lambda x: "Match", axis = 1 )
#print(qualifiesMatch)
qualifiesMatch["F1"] = qualifiesMatch.apply (lambda x: x.EM, axis = 1)
subOnlyQualifies["EM"] = subOnlyQualifies.apply (lambda x: penalty, axis = 1)
subOnlyQualifies["F1"] = subOnlyQualifies.apply (lambda x: penalty, axis = 1)
subOnlyQualifies["matchType"] = subOnlyQualifies.apply (lambda x: "Sub only", axis = 1 )
goldOnlyQualifies["EM"] = goldOnlyQualifies.apply (lambda x: penalty, axis = 1)
goldOnlyQualifies["F1"] = goldOnlyQualifies.apply (lambda x: penalty, axis = 1)
goldOnlyQualifies["matchType"] = goldOnlyQualifies.apply (lambda x: "Gold only", axis = 1 )

modsMatches["matchType"] = modsMatches.apply (lambda x: "Match", axis = 1 )
modsMatches["F1"] = modsMatches.apply (lambda x: x.EM, axis = 1)
subOnlyMods["EM"] = subOnlyMods.apply (lambda x: penalty, axis = 1)
subOnlyMods["F1"] = subOnlyMods.apply (lambda x: penalty, axis = 1)
subOnlyMods["matchType"] = subOnlyMods.apply (lambda x: "Sub only", axis = 1 )
goldOnlyMods["EM"] = goldOnlyMods.apply (lambda x: penalty, axis = 1)
goldOnlyMods["F1"] = goldOnlyMods.apply (lambda x: penalty, axis = 1)
goldOnlyMods["matchType"] = goldOnlyMods.apply (lambda x: "Gold only", axis = 1 )
modsMatches["relType"] = modsMatches.apply (lambda x: "modifier", axis = 1)
subOnlyMods["relType"] = subOnlyMods.apply (lambda x: "modifier", axis = 1)
goldOnlyMods["relType"] = goldOnlyMods.apply (lambda x: "modifier", axis = 1)

# Concatenate the whole darn thing by building an array of these dataframes
# Selecting the fields we need, and renaming columns as needed.
wrk1array = [quantityMatches[["docId", "matchType", "annotType", "EM", "maxF1"]].rename(columns={"maxF1":"F1", "annotType":"type"}),
             subOnlyQuants[["docId", "matchType", "annotType", "EM", "F1"]].rename(columns={"annotType":"type"}),
             goldOnlyQuants[["docId", "matchType", "annotType", "EM", "F1"]].rename(columns={"annotType":"type"}),
             unitMatches[["docId", "matchType", "annotType", "EM", "F1"]].rename(columns={"annotType":"type"}),
             subOnlyUnits[["docId", "matchType", "annotType", "EM", "F1"]].rename(columns={"annotType":"type"}),
             goldOnlyUnits[["docId", "matchType", "annotType", "EM", "F1"]].rename(columns={"annotType":"type"}),
             entityMatches[["docId", "matchType", "annotType", "EM", "maxF1"]].rename(columns={"maxF1":"F1", "annotType":"type"}),
             subOnlyEntities[["docId", "matchType", "annotType", "EM", "F1"]].rename(columns={"annotType":"type"}),
             goldOnlyEntities[["docId", "matchType", "annotType", "EM", "F1"]].rename(columns={"annotType":"type"}),
             propertyMatches[["docId", "matchType", "annotType", "EM", "maxF1"]].rename(columns={"maxF1":"F1", "annotType":"type"}),
             subOnlyProperties[["docId", "matchType", "annotType", "EM", "F1"]].rename(columns={"annotType":"type"}),
             goldOnlyProperties[["docId", "matchType", "annotType", "EM", "F1"]].rename(columns={"annotType":"type"}),
             qualifierMatches[["docId", "matchType", "annotType", "EM", "maxF1"]].rename(columns={"maxF1":"F1", "annotType":"type"}),
             subOnlyQualifiers[["docId", "matchType", "annotType", "EM", "F1"]].rename(columns={"annotType":"type"}),
             goldOnlyQualifiers[["docId", "matchType", "annotType", "EM", "F1"]].rename(columns={"annotType":"type"}),
             hasQuantMatch[["docId", "matchType", "relType", "EM", "F1"]].rename(columns={"relType":"type"}),
             subOnlyHasQuant[["docId", "matchType", "relType", "EM", "F1"]].rename(columns={"relType":"type"}),
             goldOnlyHasQuant[["docId", "matchType", "relType", "EM", "F1"]].rename(columns={"relType":"type"}),
             hasPropMatch[["docId", "matchType", "relType", "EM", "F1"]].rename(columns={"relType":"type"}),
             subOnlyHasProp[["docId", "matchType", "relType", "EM", "F1"]].rename(columns={"relType":"type"}),
             goldOnlyHasProp[["docId", "matchType", "relType", "EM", "F1"]].rename(columns={"relType":"type"}),
             qualifiesMatch[["docId", "matchType", "relType", "EM", "F1"]].rename(columns={"relType":"type"}),
             subOnlyQualifies[["docId", "matchType", "relType", "EM", "F1"]].rename(columns={"relType":"type"}),
             goldOnlyQualifies[["docId", "matchType", "relType", "EM", "F1"]].rename(columns={"relType":"type"}),
             modsMatches[["docId", "matchType", "relType", "EM", "F1"]].rename(columns={"relType":"type"}),
             subOnlyMods[["docId", "matchType", "relType", "EM", "F1"]].rename(columns={"relType":"type"}),
             goldOnlyMods[["docId", "matchType", "relType", "EM", "F1"]].rename(columns={"relType":"type"})
            ]

# Now we'll concatenate that into the final scoring dataframe.
wrk1score = pd.concat(wrk1array, ignore_index=True)


# Recall from the arguments, we have modes in which this scoring is run.
# That determines how averages are calculated.
# The default is "overall", which averages everything.
# The overall F1 is the score used on the leaderboard.

#parser.add_argument('-m', '--mode', help='Mode to run scoring: overall, class, doc, or both; default is overall.', default="overall")

# In overall mode, we count true positives, false positives, and false negatives
# From the Match, Sub only, and Gold only sets.
# We use this to determine a precision and recall, as well as an F-measure
# Finally, we give you your overall EM and Overlap score.
print("Working in mode " + args.mode)
print(wrk1score.shape)
print(wrk1score.columns)

cats = {}
filecats = open("../fileCategories.txt", "r")
lines = filecats.readlines()
for line in lines:
    cats[line.split('\t')[0]] = line.split('\t')[1].rstrip()
filecats.close()

wrk1score["subject"] = wrk1score.apply (lambda x: cats[x['docId'].split("-")[0]], axis = 1)

# for index, row in wrk1score.iterrows():
#     print(row['docId'].split('-')[0])
#     print(cats[row['docId'].split('-')[0]])
#     print(row['subject'].split('-')[0])


if args.mode == "overall":
    tp = len(wrk1score.loc[wrk1score["matchType"] == "Match"].index)
    fp = len(wrk1score.loc[wrk1score["matchType"] == "Sub only"].index)
    fn = len(wrk1score.loc[wrk1score["matchType"] == "Gold only"].index)
    print("True positives (matching rows): " + str(tp))
    print("False positives (submission only): " + str(fp))
    print("False negatives (gold only): " + str(fn))
    print("")
    if tp+fp == 0:
        print("Submission has no data.")
        print("")
    elif tp == 0:
        print("Submission has no matches against gold data")
    else:
        precision = tp / (tp+fp)
        recall = tp / (tp + fn)
        fmeas = (2 * precision * recall) / (precision + recall)

        print("Precision: " + str(precision))
        print("Recall: " + str(recall))
        print("F-measure: " + str(fmeas))
        print("")

    print("Overall Score Exact Match: " + str(wrk1score["EM"].mean()))
    print("Overall Score F1 (Overlap): " + str(wrk1score["F1"].mean()))
# In class mode, we provide the same data
# But with micro-averages at the class level.
elif args.mode == "class":
    for annotType in (["Quantity", "MeasuredEntity", "MeasuredProperty", "Qualifier",
                      "Unit", "modifier", "HasQuantity", "HasProperty", "Qualifies"]):
        print("Processing " + annotType)
        tp = len(wrk1score.loc[((wrk1score["matchType"] == "Match") &
                                (wrk1score["type"] == annotType))].index)
        fp = len(wrk1score.loc[((wrk1score["matchType"] == "Sub only") &
                                (wrk1score["type"] == annotType))].index)
        fn = len(wrk1score.loc[((wrk1score["matchType"] == "Gold only") &
                                (wrk1score["type"] == annotType))].index)
        if tp+fp == 0:
            print("Submission has no data for " + annotType)
            print("")
        elif tp == 0:
            print("Submission has no matches against gold data for " + annotType)
        else:
            print("True positives (matching rows) for " + annotType + ": " + str(tp))
            print("False positives (submission only) for " + annotType + ": " + str(fp))
            print("False negatives (gold only) for " + annotType + ": " + str(fn))
            print("")
            precision = tp / (tp+fp)
            print("Precision for " + annotType + ": " + str(precision))
            recall = tp / (tp + fn)
            print("Recall for " + annotType + ": " + str(recall))
            fmeas = (2 * precision * recall) / (precision + recall)
            print("F-measure for " + annotType + ": " + str(fmeas))
            print("")


        print("Exact Match Score for " + annotType + ": " +
              str(wrk1score.loc[wrk1score["type"] == annotType]["EM"].mean()))
        print("F1 (Overlap) Score for " + annotType + ": " +
              str(wrk1score.loc[wrk1score["type"] == annotType]["F1"].mean()))
        print("")

elif args.mode == "sub" or args.mode == "subject":
    for subject in (["Agriculture", "Astronomy", "Biology", "Chemistry",
                     "Computer Science", "Earth Science", "Engineering",
                     "Materials Science", "Mathematics", "Medicine"]):
        print("Processing " + subject)
        tp = len(wrk1score.loc[((wrk1score["matchType"] == "Match") &
                                (wrk1score["subject"] == subject))].index)
        fp = len(wrk1score.loc[((wrk1score["matchType"] == "Sub only") &
                                (wrk1score["subject"] == subject))].index)
        fn = len(wrk1score.loc[((wrk1score["matchType"] == "Gold only") &
                                (wrk1score["subject"] == subject))].index)
        if tp+fp == 0:
            print("Submission has no data for " + subject)
            print("")
        elif tp == 0:
            print("Submission has no matches against gold data for " + subject)
        else:
            print("True positives (matching rows) for " + subject + ": " + str(tp))
            print("False positives (submission only) for " + subject + ": " + str(fp))
            print("False negatives (gold only) for " + subject + ": " + str(fn))
            print("")
            precision = tp / (tp+fp)
            print("Precision for " + subject + ": " + str(precision))
            recall = tp / (tp + fn)
            print("Recall for " + subject + ": " + str(recall))
            fmeas = (2 * precision * recall) / (precision + recall)
            print("F-measure for " + subject + ": " + str(fmeas))
            print("")


        print("Exact Match Score for " + subject + ": " +
              str(wrk1score.loc[wrk1score["subject"] == subject]["EM"].mean()))
        print("F1 (Overlap) Score for " + subject + ": " +
              str(wrk1score.loc[wrk1score["subject"] == subject]["F1"].mean()))
        print("")


# Because it might help teams refine their submissions, during the training period
# You can run this at the document level as well.
# Say you have 20 paragraphs held out as an initial dev set
# You can run your predictions on that batch here, and see your per paragraph scores
elif args.mode == "doc":
    for docid in wrk1score.docId.unique():
        print("Exact Match Score for " + docid + ": " +
              str(wrk1score.loc[wrk1score["docId"] == docid]["EM"].mean()))
        print("F1 (Overlap) Score for " + docid + ": " +
              str(wrk1score.loc[wrk1score["docId"] == docid]["F1"].mean()))
# It's a bit verbose, but you can also see per class per document.
elif args.mode == "both" or args.mode == "classdoc":
    for docid in wrk1score.docId.unique():
        for annotType in (["Quantity", "MeasuredEntity", "MeasuredProperty", "Qualifier",
                              "Unit", "modifier", "HasQuantity", "HasProperty", "Qualifies"]):
            print("Exact Match Score for " + docid + " for " + annotType + ": " +
                  str(wrk1score.loc[((wrk1score["docId"] == docid) & (wrk1score["type"] == annotType))]["EM"].mean()))
            print("F1 (Overlap) Score for " + docid + " for " + annotType + ": " +
                  str(wrk1score.loc[((wrk1score["docId"] == docid) & (wrk1score["type"] == annotType))]["F1"].mean()))
            print("")
