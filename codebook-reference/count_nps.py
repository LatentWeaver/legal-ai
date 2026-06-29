from collections import Counter

import nltk
import spacy
from joblib import Parallel
from nltk import word_tokenize
from spacy.matcher.matcher import Matcher
import string
import pandas as pd
import re
import unidecode
import swifter
import matplotlib.pyplot as plt
import math
from sklearn.cluster import KMeans
import numpy as np

codebook_df = pd.read_csv('../../data/NP_codebook.csv')
# sort by frequency
codebook_df = codebook_df.sort_values(by='Frequency', ascending=False).reset_index(drop=True)
# keep only the words that appear more than 1000 times
# codebook_df = codebook_df[codebook_df['Frequency'] >= 200].reset_index(drop=True)

unigram_df = codebook_df[codebook_df['phrase'].swifter.apply(lambda x: len(str(x).split()) == 1)].reset_index(drop=True)
# take the log of frequency column and assign it to new column called log_frequency
unigram_df['log_frequency'] = unigram_df['Frequency'].swifter.apply(lambda x: math.log(x))

from kneed import KneeLocator
x = unigram_df['log_frequency']
y = unigram_df.index
kn = KneeLocator(x, y, curve='convex', direction='decreasing', S=2)
print(kn.knee)

# number of values higher than knee
cut_point = len(unigram_df[unigram_df['log_frequency'] > kn.knee])

# draw a line chart of Frequency column where x axis is the index
fig = unigram_df['log_frequency'].plot.line()
# draw a vertical line at 2000
fig.axvline(x=cut_point, color='r', linestyle='--')
plt.show()
# keep first 2000 rows of unigrams
unigram_df = unigram_df.iloc[:cut_point]

# filter only 1 where value is 0/1
unigram_df = unigram_df[unigram_df['0/1'] == 1].reset_index(drop=True)


# create filtered_df from codebook_df where after split any of unigram is inside them or the column 0/1 is 1
filtered_df = codebook_df[codebook_df['phrase'].swifter.apply(lambda x: any([unigram in str(x).split() for unigram in unigram_df['phrase']]))].reset_index(drop=True)

# take the log of frequency column and assign it to new column called log_frequency
filtered_df['log_frequency'] = np.log(filtered_df['Frequency'])

# draw a line chart of Frequency column where x axis is the index
filtered_df['log_frequency'].plot.line()
plt.show()

# take first 20000 of filtered_df
filtered_top_df = filtered_df.iloc[:100000]
# draw histogram of the word frequencies where word is in phrase column and frequency is in Frequency column
filtered_df.describe()
filtered_top_df.describe()

ni_bigrams_df = codebook_df[codebook_df.apply(lambda x: len(str(x['phrase']).split()) != 1 and x['0/1'] == 1, axis=1)].reset_index(drop=True)

not_computed = set(ni_bigrams_df['phrase'].unique()).difference(set(filtered_top_df['phrase'].unique()))
ni_bigrams_df = ni_bigrams_df[ni_bigrams_df['phrase'].apply(lambda x: x in not_computed)].reset_index(drop=True)

tweets_clean_df = pd.read_csv('../../data/Races_ALM_BLM_Text_processed.csv')

# concat all tweets into one document where it is in processed_tweet_text column

concatted_doc = '. '.join(tweets_clean_df['processed_tweet_text'].astype(str))
print(len(concatted_doc))
len(tweets_clean_df.index)

# Building Aho-Corasick automaton for fast counting
import ahocorasick
automaton = ahocorasick.Automaton()
for idx, key in enumerate(filtered_top_df['phrase']):
    automaton.add_word(' ' + key + ' ', (idx, ' ' + key + ' '))
automaton.make_automaton()

# Finding all matches using the Aho-Corasick automaton
matches = []
for end_index, (insert_order, original_value) in automaton.iter(concatted_doc):
    matches.append(original_value)

# Counting occurrences of each phrase
from collections import Counter
phrase_counts = Counter(matches)
phrase_counts_dict = dict(phrase_counts)
filtered_top_df['new_frequency'] = filtered_top_df['phrase'].swifter.apply(lambda x: phrase_counts_dict.get(' ' + x + ' ', 0))
ni_bigrams_df['new_frequency'] = ni_bigrams_df['phrase'].swifter.apply(lambda x: phrase_counts_dict.get(' ' + x + ' ', 0))


concatted_doc.count(' black cops ')

# concat filtered_top_df and ni_bigrams_df
result_df = pd.concat([filtered_top_df, ni_bigrams_df], ignore_index=True)

# sort filtered_top_df by new_frequency column
result_df = result_df.sort_values(by='new_frequency', ascending=False).reset_index(drop=True)
result_df.to_csv('data/newly_counted_frequencies_on_clean_viral_data_includeds.csv', index=False)

# print count where new_frequency is not below 100
print(len(filtered_top_df[filtered_top_df['new_frequency'] > 100]))