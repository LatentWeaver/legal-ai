import warnings

from pandas.errors import SettingWithCopyWarning

warnings.simplefilter(action='ignore', category=SettingWithCopyWarning)

import pandas as pd

from spacy_ngram_extractor import SpacyNgramExtractor


# Extract ngrams up to the specified maximum n and their counts from the corpus file
ngram_df = pd.read_csv('../../data/newly_counted_frequencies_on_clean_viral_data_includeds.csv')

ngram_copy_df = ngram_df.copy()

# filter the df where count is 0
zero_count_df = ngram_df[ngram_df['new_frequency'] == 0].sort_values(by='Frequency', ascending=False)
ngram_df = ngram_df[ngram_df['new_frequency'] > 0]
ngram_df['old_frequency'] = ngram_df['Frequency']
ngram_df['ngram'] = ngram_df['phrase'].apply(lambda x: tuple(x.split()))
ngram_count_dict = dict(zip(ngram_df['ngram'], ngram_df['new_frequency']))

# find the most occuring unigram by looking at the ngram_count_dict. the ngram column is a tuple. so we are looking at all elements in the tuple and return the one with the highest value
ngram_df['head_unigram'] = ngram_df['ngram'].apply(lambda x: max(x, key=lambda y: ngram_count_dict.get((y,), 0)))
ngram_df['head_unigram_count'] = ngram_df['head_unigram'].apply(lambda x: ngram_count_dict.get((x,), 0))
ngram_df['ngram_length'] = ngram_df['ngram'].apply(lambda x: len(x))

ngram_df['new_algorithm_frequency'] = ngram_df['new_frequency']

unigrams = ngram_df[ngram_df['ngram_length'] == 1]['phrase']
for target_unigram in unigrams:
    target_df = ngram_df[ngram_df['head_unigram'] == target_unigram]
    target_df = target_df.sort_values(by='new_frequency', ascending=False)
    target_df_bigrams = target_df[target_df['ngram'].apply(lambda x: len(x) != 1)]
    if target_df_bigrams.empty:
        continue
    unigram_count = target_df[target_df['ngram'].apply(lambda x: len(x) == 1)]['new_frequency'].iloc[0]
    unigram_total_count = unigram_count # + target_df_bigrams['count'].sum()
    max_bigram_count = target_df_bigrams['new_frequency'].iloc[0]
    # cumsum is the cumulative sum of the count column where first row is 0
    target_df_bigrams['cumsum'] = target_df_bigrams['new_frequency'].cumsum()
    # target_df_bigrams['cumsum'].iloc[0] = 0

    # inverse_cumsum is the cumulative sum of the count column where first row is the sum of all counts
    target_df_bigrams['included'] = target_df_bigrams['cumsum'].apply(lambda x: True if x <= max(unigram_total_count/2, unigram_count-max_bigram_count+1) else False)

    # set included False to True where the first row with included False is found
    target_df_bigrams.loc[target_df_bigrams['included'] == False, 'included'] = target_df_bigrams.loc[target_df_bigrams['included'] == False, 'included'].shift(1)

    # fillna with True for the column included
    target_df_bigrams['included'] = target_df_bigrams['included'].fillna(True)

    # get the cumsum value of last included = True in the target_df_bigrams
    if target_df_bigrams[target_df_bigrams['included'] == True].empty:
        continue

    try:
        last_included_cumsum = target_df_bigrams[target_df_bigrams['included'] == True]['cumsum'].iloc[-1]
        included_bigram_count = target_df_bigrams['included'].sum()

        # subtract the last_included_cumsum value from new_frequency column of ngram_copy_df where ngram is target_unigram
        ngram_df.loc[ngram_copy_df['phrase'] == target_unigram, 'new_algorithm_frequency'] = ngram_df.loc[ngram_df['phrase'] == target_unigram, 'new_frequency'] - last_included_cumsum
        ngram_df.loc[ngram_copy_df['phrase'] == target_unigram, 'included_bigram_count'] = included_bigram_count
    except:
        print(f'error for {target_unigram}')
        break

# sort on new_algorithm_frequency column

# plot line chart of included_bigram_count in sorted by filtering out nan values
values_to_draw = ngram_df['included_bigram_count'].sort_values(ascending=True).reset_index(drop=True).dropna()

# use values less than 500
values_to_draw = values_to_draw[values_to_draw < 150]

# draw as histogram

values_to_draw.describe()
values_to_draw.plot.hist(bins=100)
import matplotlib.pyplot as plt
plt.show()
plt.cla()
ngram_df.to_csv('data/newly_counted_frequencies_on_clean_viral_data_includes_with_algorithm.csv', index=False)