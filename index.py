# Hi everyone
# Happy that was copied from 
# https://github.com/blazerer/pingEloBot

import json
import os
import logging
import re
import boto3
import telebot
import numpy as np
import datetime

ADMIN_HANDLER = os.getenv("ADMIN_HANDLER")
GROUP_NAME = os.getenv("GROUP_NAME")
BOT_TOKEN = os.getenv("BOT_TOKEN")
S3_ACCESS_KEY_ID = os.getenv("S3_ACCESS_KEY_ID")
S3_SECRET_ACCESS_KEY = os.getenv("S3_SECRET_ACCESS_KEY")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL")
S3_REGION = os.getenv("S3_REGION")

if (ADMIN_HANDLER is None):
    raise Exception("ADMIN_HANDLER is required")

if (GROUP_NAME is None):
    raise Exception("GROUP_NAME is required")

if (BOT_TOKEN is None):
    raise Exception("BOT_TOKEN is required")

if (S3_ACCESS_KEY_ID is None):
    raise Exception("S3_ACCESS_KEY_ID is required")

if (S3_SECRET_ACCESS_KEY is None):
    raise Exception("S3_SECRET_ACCESS_KEY is required")

if (S3_BUCKET_NAME is None):
    raise Exception("S3_BUCKET_NAME is required")

if (S3_ENDPOINT_URL is None):
    raise Exception("S3_ENDPOINT_URL is required")

if (S3_REGION is None):
    raise Exception("S3_REGION is required")

QUEUE_DIR = 'players_queue'
PLAYERS_DIR = 'players_stats'
RIVALS_DIR = 'rivals_stats'
START_RATING = 1000
ELO_BASE = 10.0
ELO_POWER_DENOMINATOR = 400.0
ELO_MULTIPLIER = 40
ACTIVE_TOP_DAYS = 14


class QueueInfo:
    def __init__(self, storage_client, bucket_name, queue_dir):
        self.storage_client = storage_client
        self.bucket_name = bucket_name
        self.queue_dir = queue_dir

    def book_table(self, name, mid, cid):
        self.storage_client.put_object(
            Bucket=self.bucket_name,
            Key=f"{self.queue_dir}/{name}", Body=f"{mid},{cid}")

    def leave_table(self, name):
        try:
            self.storage_client.delete_object(
                Bucket=self.bucket_name, Key=f"{self.queue_dir}/{name}")
        except Exception as e:
            pass

    def get_booking_info(self, name):
        try:
            booking_info = self.storage_client.get_object(
                Bucket=self.bucket_name,
                Key=f"{self.queue_dir}/{name}")['Body'].read().decode('utf-8')
            mid, cid = (int(x) for x in booking_info.split(','))
            return (mid, cid)
        except Exception as e:
            return None

    def waiting_list(self):
        try:
            response = self.storage_client.list_objects(
                Bucket=self.bucket_name, Prefix=self.queue_dir)
            if 'Contents' not in response:
                return []

            keys = [
                (
                    key['LastModified'],
                    key['Key'].replace(f"{self.queue_dir}/", ''))
                for key in response['Contents']
                if key['Key'] != f"{self.queue_dir}/"]
            return [handler for (_, handler) in sorted(keys)]

        except Exception as e:
            return None


class RatingInfo:
    def __init__(self, storage_client, bucket_name,
                 ratings_dir, rivals_dir, active_top_days):
        self.storage_client = storage_client
        self.bucket_name = bucket_name
        self.ratings_dir = ratings_dir
        self.rivals_dir = rivals_dir
        self.active_top_days = active_top_days

    def get(self, name):
        try:
            r = self.storage_client.get_object(
                Bucket=self.bucket_name,
                Key=f"{self.ratings_dir}/{name}")['Body'].read().decode()
            values = r.split(',')
            return (int(values[0]), int(values[1]), int(values[2]))
        except Exception as e:
            return None

    def set(self, name, rating, win, lose):
        self.storage_client.put_object(
            Bucket=self.bucket_name,
            Key=f"{self.ratings_dir}/{name}",
            Body=f'{rating},{win},{lose}')

    def delete(self, name):
        try:
            self.storage_client.delete_object(
                Bucket=self.bucket_name,
                Key=f"{self.ratings_dir}/{name}")
        except Exception as e:
            pass

    def top(self):
        try:
            response = self.storage_client.list_objects(
                Bucket=self.bucket_name,
                Prefix=self.ratings_dir)

            if 'Contents' not in response:
                return []

            horizon = (datetime.datetime.now() -
                       datetime.timedelta(self.active_top_days))
            horizon_str = f'{horizon}'[:23]

            keys = [key['Key'].replace(f"{self.ratings_dir}/", '')
                    for key in response['Contents']
                    if (key['Key'] != f"{self.ratings_dir}/" and
                        str(key['LastModified'])[:23] > horizon_str)
                    ]

            if len(keys) == 0:
                return []

            top = []
            for k in keys:
                r = self.get(k)
                top.append((r, k))

            top.sort(reverse=True)
            return top

        except Exception as e:
            return None

    def get_rivals_stats(self, name_1, name_2):
        try:
            turned = False
            if name_1 > name_2:
                name_1, name_2 = name_2, name_1
                turned = True

            joint_name = f"{name_1}+{name_2}"

            r = self.storage_client.get_object(
                Bucket=self.bucket_name,
                Key=f"{self.rivals_dir}/{joint_name}")['Body'].read().decode()
            values = r.split(',')

            if turned:
                values[0], values[1] = values[1], values[0]

            return (int(values[0]), int(values[1]))

        except Exception as e:
            return None

    def increment_rivals_stats(self, name_1, name_2, first_won):
        ratings = self.get_rivals_stats(name_1, name_2) or (0, 0)

        if first_won:
            ratings = ratings[0]+1, ratings[1]
        else:
            ratings = ratings[0], ratings[1]+1

        self.set_rivals_stats(name_1, name_2, ratings[0], ratings[1])

    def set_rivals_stats(self, name_1, name_2, win_1, win_2):
        if name_1 > name_2:
            name_1, name_2 = name_2, name_1
            win_1, win_2 = win_2, win_1

        joint_name = f"{name_1}+{name_2}"

        self.storage_client.put_object(
            Bucket=self.bucket_name,
            Key=f"{self.rivals_dir}/{joint_name}", Body=f'{win_1},{win_2}')


bot = telebot.TeleBot(BOT_TOKEN, threaded=False)

boto_session = boto3.session.Session(
    aws_access_key_id=S3_ACCESS_KEY_ID,
    aws_secret_access_key=S3_SECRET_ACCESS_KEY
)

s3_storage_client = boto_session.client(
    service_name='s3',
    endpoint_url=S3_ENDPOINT_URL,
    region_name=S3_REGION,
)

queue = QueueInfo(s3_storage_client, S3_BUCKET_NAME, QUEUE_DIR)
ratings = RatingInfo(s3_storage_client, S3_BUCKET_NAME,
                     PLAYERS_DIR, RIVALS_DIR, ACTIVE_TOP_DAYS)

# ======================= RATING METHODS =======================


@bot.message_handler(commands=['register_me'])
def register_handler(message):
    """
    Registering a new user in the competition
    """
    sender = message.from_user.username

    if rating := ratings.get(sender):
        bot.reply_to(
            message,
            f"Seems you've already registered and your rating is {rating}.")
        return

    if message.chat.title != GROUP_NAME:
        bot.reply_to(
            message,
            f'Sorry, the registration is allowed only at the group {GROUP_NAME}.')
        return

    ratings.set(sender, START_RATING, 0, 0)
    bot.reply_to(message, f"Registered @{sender} with rating = {START_RATING}.")


@bot.message_handler(commands=['delete_me'])
def delete_handler(message):
    """
    Removing user from the competition
    """
    sender = message.from_user.username
    ratings.delete(sender)
    bot.reply_to(
        message,
        f"Sorry to see you go, @{sender}. Your rating is deleted from the top.")


@bot.message_handler(commands=['my_rating'])
def my_rating_handler(message):
    """
    Print rating of sender
    """
    sender = message.from_user.username

    if rating := ratings.get(sender):
        bot.reply_to(
            message,
            f"Your rating is {rating[0]} | {rating[1]} | {rating[2]} .")
    else:
        bot.reply_to(message, f"Seems @{sender} hasn't registered yet.")


@bot.message_handler(commands=['rating_of'])
def rating_of_handler(message):
    """
    Print rating of user
    """
    if m := re.match(r"/rating_of @([a-zA-Z0-9_]+)", message.text):
        player = m.group(1)
    else:
        bot.reply_to(
            message,
            f'Something\'s wrong. You should use "/rating_of @someone".')
        return

    if rating := ratings.get(player):
        bot.reply_to(
            message,
            f"{player}'s rating is {rating[0]} | {rating[1]} | {rating[2]}.")
    else:
        bot.reply_to(message, f"Seems {player} hasn't registered yet.")


@bot.message_handler(commands=['stats_vs'])
def stats_vs_handler(message):
    """
    Print personal stats between user and their rival
    """
    sender = message.from_user.username

    if m := re.match(r"/stats_vs @([a-zA-Z0-9_]+)", message.text):
        player = m.group(1)
    else:
        bot.reply_to(
            message,
            f'Something\'s wrong. You should use "/stats_vs @someone".')
        return

    if player == sender:
        bot.reply_to(
            message,
            f"Not sure that {sender} could play with themself")
        return

    if stats := ratings.get_rivals_stats(sender, player):
        bot.reply_to(message, f"{sender} - {stats[0]} | {stats[1]} - {player}")
    else:
        bot.reply_to(
            message,
            f"Seems {sender} and {player} haven't played against each other yet.")

@bot.message_handler(commands=['top'])
def top_handler(message):
    """
    Print the current top ratings.
    It's better to remove this method if you want to use bot in large group or just cache results
    """
    top = ratings.top()

    if top:
        top_size = len(top)

        if 0 < top_size:
            top[0] = (top[0][0], top[0][1] + ' 🥇')
        if 1 < top_size:
            top[1] = (top[1][0], top[1][1] + ' 🥈')
        if 2 < top_size:
            top[2] = (top[2][0], top[2][1] + ' 🥉')

        top_repr = "\n".join(
            [f"{handler} = {rates[0]} | {rates[1]} | {rates[2]}"
                for (rates, handler) in top])

        prefix_str = np.random.choice(
            ['Active Top\nPlayer = Pts | W | L',
                'People who might work instead of this'],
            p=[0.9, 0.1])

        bot.reply_to(message, f"{prefix_str}:\n{top_repr}")
    else:
        bot.reply_to(message, f"Can't get anything")

# ======================= QUEUE METHODS =======================


@bot.message_handler(commands=['queue'])
def queue_handler(message):
    """
    Printing queue's members
    """
    current_queue = queue.waiting_list()

    if current_queue:
        table_owner = current_queue[0]
        queue_repr = "\n".join(f"{x}" for x in current_queue[1:])
        waiting_list = f"The waiting list:\n{queue_repr}" if queue_repr else "The waiting list is empty."

        bot.reply_to(message, f"Expected that {table_owner} is playing now.\n{waiting_list}")
    else:
        bot.reply_to(message, f"The queue is empty.")


@bot.message_handler(commands=['book'])
def book_handler(message):
    """
    Adding a user into the queue
    """
    sender = message.from_user.username
    current_queue = queue.waiting_list()

    if current_queue and sender in current_queue:
        if sender == current_queue[0]:
            bot.reply_to(message, f"But you should be playing right now, huh?")
        else:
            bot.reply_to(message, f"But you're alredy in the queue.")
        return

    queue.book_table(sender, message.message_id, message.chat.id)

    current_queue.append(sender)

    if sender == current_queue[0]:
        bot.reply_to(
            message,
            f"Table should be empty. You may start playing, @{sender}")
    else:
        queue_repr = "\n".join(f"{x}" for x in current_queue[1:])
        waiting_list = f"The waiting list:\n{queue_repr}" if queue_repr else "The waiting list is empty."
        bot.reply_to(
            message,
            f"Table is booked.\nExpected that {current_queue[0]} is playing now\n{waiting_list}")


@bot.message_handler(commands=['leave'])
def leave_handler(message):
    """
    Leaving the queue and notifying the next user
    """
    sender = message.from_user.username
    current_queue = queue.waiting_list()

    if current_queue:
        if sender not in current_queue:
            bot.reply_to(message, f"But you aren't in the queue now.")
            return

        position = current_queue.index(sender)
        queue.leave_table(sender)
        bot.reply_to(message, f"Thanks for letting us know, @{sender}.")

        if len(current_queue) > 1 and position == 0:
            handler_to_notify = current_queue[1]
            ids = queue.get_booking_info(handler_to_notify)
            if ids:
                mid, cid = ids
                bot.send_message(
                    cid,
                    f"You're the next in the queue, @{handler_to_notify}.",
                    reply_to_message_id=mid)
    else:
        bot.reply_to(message, f"But the queue is empty...")


@bot.message_handler(commands=['clean_queue'])
def clean_queue_handler(message):
    """
    Cleaning queue
    """
    current_queue = queue.waiting_list()
    if current_queue:
        waiting_list = ", ".join(f"@{handler}" for handler in current_queue)
        bot.reply_to(
            message,
            f"Ok, going to clean up the queue with all these guys: {waiting_list}.")
        for handler in current_queue:
            queue.leave_table(handler)
    else:
        bot.reply_to(message, f"But the queue is empty")

# ======================= RATING MODIFICATION METHODS =======================


@bot.message_handler(commands=['played'])
def played_handler(message):
    """
    Sending a game results
    """
    player_1 = message.from_user.username
    if message.chat.title != GROUP_NAME and player_1 != ADMIN_HANDLER:
        bot.reply_to(
            message,
            f'Sorry, rating games should be posted only into the group.')
        return

    if m := re.match(r"/played @([a-zA-Z0-9_]+) (\d+)-(\d+)", message.text):
        player_2 = m.group(1)
        a = int(m.group(2))
        b = int(m.group(3))
    else:
        bot.reply_to(
            message,
            f'Something\'s wrong. You should text this command in such way: "/played @opponent 2-1" or any other valid score (from range [0..3]).')
        return

    if a == b or a < 0 or a > 3 or b < 0 or b > 3:
        bot.reply_to(
            message,
            f'Excpecting to have the first number={a} differs from the second one={b} and from the range [0..3].')
        return

    if player_2 == player_1:
        bot.reply_to(message, f"You think I'm funny, yeah?.")
        return

    if not (ratings_1 := ratings.get(player_1)):
        bot.reply_to(
            message,
            f"Seems there is no rating for @{player_1}, need to register at first.")
        return

    if not (ratings_2 := ratings.get(player_2)):
        bot.reply_to(
            message,
            f"Seems there is no rating for @{player_2}, need to register at first.")
        return

    rating_1, wins_1, loses_1 = ratings_1
    rating_2, wins_2, loses_2 = ratings_2

    game_score_adjustment = 0.1 + abs(a - b) * 0.2
    if a > b:
        adjustment_1 = 0.5 + game_score_adjustment
        adjustment_2 = 0.5 - game_score_adjustment
        wins_1 += 1
        loses_2 += 1
    else:
        adjustment_1 = 0.5 - game_score_adjustment
        adjustment_2 = 0.5 + game_score_adjustment
        wins_2 += 1
        loses_1 += 1

    def E(rating_a, rating_b):
        return 1.0 / (1 + ELO_BASE ** ((rating_b - rating_a) / ELO_POWER_DENOMINATOR))

    new_rating_1 = int(rating_1 + ELO_MULTIPLIER * (adjustment_1 - E(rating_1, rating_2)))
    new_rating_2 = int(rating_2 + ELO_MULTIPLIER * (adjustment_2 - E(rating_2, rating_1)))

    ratings.set(player_1, new_rating_1, wins_1, loses_1)
    ratings.set(player_2, new_rating_2, wins_2, loses_2)
    ratings.increment_rivals_stats(player_1, player_2, a > b)

    message_from_bot = np.random.choice(['Cheers!', 'Nice game!', 'I\'ve seen better...', 'I\'m quite dissapointed of that.'], p=[0.75, 0.2, 0.04, 0.01])

    bot.reply_to(message, f"Rating updates from @{player_1} {a}-{b} @{player_2}:\n"
        f"@{player_1} {rating_1} -> {new_rating_1}\n"
        f"@{player_2} {rating_2} -> {new_rating_2}\n"
        f"{message_from_bot}\n"
        f"#games #{player_1}_games #{player_2}_games")


@bot.message_handler(commands=['set_score'])
def set_score_handler(message):
    """
    Setting score for admin
    """
    sender = message.from_user.username

    if sender != ADMIN_HANDLER:
        bot.reply_to(message, f'Allowed only for {ADMIN_HANDLER}')
        return

    if m := re.match(r"/set_score @([a-zA-Z0-9_]+) (\d+) (\d+) (\d+)", message.text):
        player = m.group(1)
        player_score = m.group(2)
        player_win = m.group(3)
        player_lose = m.group(4)
    else:
        bot.reply_to(
            message,
            f'Something\'s wrong. You should use "/set_score @someone 1234 10 20".')
        return

    if int(player_score) <= ELO_MULTIPLIER:
        bot.reply_to(message, f'Score should be higher than {ELO_MULTIPLIER}.')
        return

    ratings.set(player, player_score, player_win, player_lose)
    bot.reply_to(message, f"@{player}'s rating = {player_score} | {player_win} | {player_lose} now.")


@bot.message_handler(commands=['set_stats_vs'])
def set_stats_vs_handler(message):
    """
    Setting stats_vs for two users for admin
    """
    sender = message.from_user.username

    if sender != ADMIN_HANDLER:
        bot.reply_to(message, f'Allowed only for {ADMIN_HANDLER}')
        return

    if m := re.match(r"/set_stats_vs @([a-zA-Z0-9_]+) @([a-zA-Z0-9_]+) (\d+) (\d+)", message.text):
        player_1 = m.group(1)
        player_2 = m.group(2)
        player_1_win = m.group(3)
        player_2_win = m.group(4)
    else:
        bot.reply_to(
            message,
            f'Something\'s wrong. You should use "/set_stats_vs @first @second 10 20".')
        return

    if player_1 == player_2:
        bot.reply_to(message, f"Not sure that {player_1} could play with themself")
        return

    ratings.set_rivals_stats(player_1, player_2, player_1_win, player_2_win)
    bot.reply_to(
        message,
        f"So, now we have {player_1} - {player_1_win} | {player_2_win} - {player_2}")

# ======================= HELP METHOD =======================


@bot.message_handler(commands=['help', 'start'])
def help_handler(message):
    """
    Print help message
    """
    bot.reply_to(
        message,
        rf"""Hey, ready to crush some enemies?
These commands will help you to save the results and book the table\*:
`/help` - Post this message
`/start` - Post this message

**Account manipulations**:
`/register_me` - Registration for the new participants **(only allowed in the group)**
`/delete_me` - Remove your rating from the competition (but not from the rivals' stats)

**Game and ratings**:
`/played @someone a-b` - Declare that you played with someone and the final score is a-b **(only allowed in the group)**
`/my_rating` - Post your rating
`/rating_of @someone` - Post someone's rating
`/stats_vs @someone` - Your personal stats against @someone
`/top` - List of top scorers

**Queue**:
`/queue` - Get the waiting list state
`/book` - Add me into the waiting list
`/leave` - Leave the table (when you finish playing) or leave the queue
`/clean_queue` - Clean the waiting list if some confusion happened

**Admin**(Only allowed for administrator of this bot):
`/set_score @someone 1 2 3` - Set top stats for @someone with rating=1, wins=2 and loses=3
`/set_stats_vs @someone1 @someone2 1 2` - Set rivals stats between @someone1 and @someone2 as 1-2

If something went wrong, please ask admin of your group ({ADMIN_HANDLER}) to fix ratings
\*We're using modifed ELO rating where the actual game score slightly amplifies the total rating change""",
        disable_web_page_preview=True,
        parse_mode="Markdown")


@bot.message_handler(regexp='^/.*')
def default_handler(message):
    """
    Print default message for unknown command
    """
    bot.reply_to(message, "Not sure, what you meant by that")


def handler(event, context):
    try:
        logging.info(f'{event=}')

        request_body_dict = json.loads(event["body"])
        update = telebot.types.Update.de_json(request_body_dict)
        bot.process_new_updates([update])
        return {
            'statusCode': 200,
            'headers': {},
            'isBase64Encoded': False
        }
    except Exception as e:
        logging.error(f'{e=}')
