--
-- PostgreSQL database dump
--

\restrict s10OvEYJLCBUUhnkN0QDTq29b5nxCFfLLJond96FLbbfmS86DU6bskADwjmIX1p

-- Dumped from database version 16.14 (Debian 16.14-1.pgdg13+1)
-- Dumped by pg_dump version 16.14 (Debian 16.14-1.pgdg13+1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: pgcrypto; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;


--
-- Name: EXTENSION pgcrypto; Type: COMMENT; Schema: -; Owner: 
--

COMMENT ON EXTENSION pgcrypto IS 'cryptographic functions';


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: books; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.books (
    book_id bigint NOT NULL,
    title text NOT NULL,
    author text,
    source_uri text,
    ingestion_status text DEFAULT 'pending'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT books_ingestion_status_check CHECK ((ingestion_status = ANY (ARRAY['pending'::text, 'registry_pass_complete'::text, 'beats_pass_complete'::text, 'failed'::text])))
);


ALTER TABLE public.books OWNER TO postgres;

--
-- Name: books_book_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.books_book_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.books_book_id_seq OWNER TO postgres;

--
-- Name: books_book_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.books_book_id_seq OWNED BY public.books.book_id;


--
-- Name: character_states; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.character_states (
    state_id bigint NOT NULL,
    character_id bigint NOT NULL,
    valid_from_paragraph_id bigint NOT NULL,
    valid_until_paragraph_id bigint,
    appearance_delta text,
    emotional_state text,
    vocal_delta_prompt text,
    profile_snapshot jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_character_state_range CHECK (((valid_until_paragraph_id IS NULL) OR (valid_until_paragraph_id <> valid_from_paragraph_id)))
);


ALTER TABLE public.character_states OWNER TO postgres;

--
-- Name: character_states_state_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.character_states_state_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.character_states_state_id_seq OWNER TO postgres;

--
-- Name: character_states_state_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.character_states_state_id_seq OWNED BY public.character_states.state_id;


--
-- Name: characters; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.characters (
    character_id bigint NOT NULL,
    book_id bigint NOT NULL,
    canonical_name text NOT NULL,
    aliases text[] DEFAULT '{}'::text[] NOT NULL,
    baseline_visual_description text NOT NULL,
    baseline_voice_description text NOT NULL,
    voice_reference_audio_uri text,
    extended_profile jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.characters OWNER TO postgres;

--
-- Name: characters_character_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.characters_character_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.characters_character_id_seq OWNER TO postgres;

--
-- Name: characters_character_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.characters_character_id_seq OWNED BY public.characters.character_id;


--
-- Name: location_states; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.location_states (
    state_id bigint NOT NULL,
    location_id bigint NOT NULL,
    valid_from_paragraph_id bigint NOT NULL,
    valid_until_paragraph_id bigint,
    atmosphere_delta text,
    lighting_state text,
    ambient_sfx_delta text,
    profile_snapshot jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_location_state_range CHECK (((valid_until_paragraph_id IS NULL) OR (valid_until_paragraph_id <> valid_from_paragraph_id)))
);


ALTER TABLE public.location_states OWNER TO postgres;

--
-- Name: location_states_state_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.location_states_state_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.location_states_state_id_seq OWNER TO postgres;

--
-- Name: location_states_state_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.location_states_state_id_seq OWNED BY public.location_states.state_id;


--
-- Name: locations; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.locations (
    location_id bigint NOT NULL,
    book_id bigint NOT NULL,
    canonical_name text NOT NULL,
    aliases text[] DEFAULT '{}'::text[] NOT NULL,
    baseline_visual_description text NOT NULL,
    baseline_ambient_sfx_prompt text NOT NULL,
    extended_profile jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.locations OWNER TO postgres;

--
-- Name: locations_location_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.locations_location_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.locations_location_id_seq OWNER TO postgres;

--
-- Name: locations_location_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.locations_location_id_seq OWNED BY public.locations.location_id;


--
-- Name: paragraph_characters; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.paragraph_characters (
    paragraph_id bigint NOT NULL,
    character_id bigint NOT NULL
);


ALTER TABLE public.paragraph_characters OWNER TO postgres;

--
-- Name: paragraphs; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.paragraphs (
    paragraph_id bigint NOT NULL,
    book_id bigint NOT NULL,
    chapter_number integer NOT NULL,
    sequence_index integer NOT NULL,
    raw_text text NOT NULL,
    active_location_id bigint,
    camera_framing text DEFAULT 'medium_shot'::text NOT NULL,
    action_summary text NOT NULL,
    dialogue_script jsonb DEFAULT '[]'::jsonb NOT NULL,
    sfx_prompts jsonb DEFAULT '[]'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_dialogue_script_is_array CHECK ((jsonb_typeof(dialogue_script) = 'array'::text)),
    CONSTRAINT chk_sfx_prompts_is_array CHECK ((jsonb_typeof(sfx_prompts) = 'array'::text)),
    CONSTRAINT paragraphs_camera_framing_check CHECK ((camera_framing = ANY (ARRAY['extreme_close_up'::text, 'close_up'::text, 'medium_shot'::text, 'wide_shot'::text, 'establishing_shot'::text, 'over_the_shoulder'::text, 'pov'::text])))
);


ALTER TABLE public.paragraphs OWNER TO postgres;

--
-- Name: paragraphs_paragraph_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.paragraphs_paragraph_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.paragraphs_paragraph_id_seq OWNER TO postgres;

--
-- Name: paragraphs_paragraph_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.paragraphs_paragraph_id_seq OWNED BY public.paragraphs.paragraph_id;


--
-- Name: books book_id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.books ALTER COLUMN book_id SET DEFAULT nextval('public.books_book_id_seq'::regclass);


--
-- Name: character_states state_id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.character_states ALTER COLUMN state_id SET DEFAULT nextval('public.character_states_state_id_seq'::regclass);


--
-- Name: characters character_id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.characters ALTER COLUMN character_id SET DEFAULT nextval('public.characters_character_id_seq'::regclass);


--
-- Name: location_states state_id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.location_states ALTER COLUMN state_id SET DEFAULT nextval('public.location_states_state_id_seq'::regclass);


--
-- Name: locations location_id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.locations ALTER COLUMN location_id SET DEFAULT nextval('public.locations_location_id_seq'::regclass);


--
-- Name: paragraphs paragraph_id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.paragraphs ALTER COLUMN paragraph_id SET DEFAULT nextval('public.paragraphs_paragraph_id_seq'::regclass);


--
-- Data for Name: books; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.books (book_id, title, author, source_uri, ingestion_status, created_at) FROM stdin;
1	Frankenstein	Mary Shelley	data/texts/shelley-frankenstein.txt	beats_pass_complete	2026-06-20 09:47:46.374283+00
2	A Christmas Carol	Charles Dickens	data/texts/dickens-a-christmas-carol.txt	beats_pass_complete	2026-06-20 10:46:23.035579+00
\.


--
-- Data for Name: character_states; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.character_states (state_id, character_id, valid_from_paragraph_id, valid_until_paragraph_id, appearance_delta, emotional_state, vocal_delta_prompt, profile_snapshot, created_at) FROM stdin;
1	1	10	11	\N	\N	\N	{"backstory": "Robert Walton is an explorer who has dedicated himself to a voyage to the North Pole, driven by a desire for discovery and a sense of adventure. He comes from a family that valued travel and exploration, though his own education was somewhat neglected.", "motivations": "He seeks to achieve glory and discovery, to fulfill a lifelong dream of exploring uncharted territories, and to find a friend who understands his ambitions.", "relationships": {"Margaret Saville": "His sister, to whom he writes letters, expressing his hopes, fears, and the loneliness he feels."}, "speech_patterns": "Walton's writing style is reflective and detailed, showing a deep emotional investment in his journey and a longing for companionship.", "personality_traits": ["Determined", "Passionate", "Thoughtful", "Lonely"]}	2026-06-20 09:57:24.518186+00
2	1	11	12	\N	\N	\N	{"backstory": "Robert Walton is an explorer who has dedicated himself to a voyage to the North Pole, driven by a desire for discovery and a sense of adventure. He comes from a family that valued travel and exploration, though his own education was somewhat neglected.", "motivations": "He seeks to achieve glory and discovery, to fulfill a lifelong dream of exploring uncharted territories, and to find a friend who understands his ambitions.", "relationships": {"Margaret Saville": "His sister, to whom he writes letters, expressing his hopes, fears, and the loneliness he feels."}, "speech_patterns": "Walton's writing style is reflective and detailed, showing a deep emotional investment in his journey and a longing for companionship.", "personality_traits": ["Determined", "Passionate", "Thoughtful", "Lonely"]}	2026-06-20 09:57:24.518186+00
827	41	776	778	\N	\N	\N	{"backstory": "Scrooge is a miserly businessman who continues to operate under the name 'Scrooge and Marley', despite Marley's death.", "motivations": "Maintaining his wealth and avoiding social interactions.", "relationships": {"Marley": "Former business partner", "Nephew": "Uncle"}, "speech_patterns": "Uses short, sharp sentences, often interjecting with 'Bah!' and 'Humbug!'", "personality_traits": ["Miserly", "Solitary", "Covetous", "Cold-hearted"]}	2026-06-20 10:54:26.207593+00
3	1	12	13	\N	\N	\N	{"backstory": "Robert Walton is an explorer who has dedicated himself to a voyage to the North Pole, driven by a desire for discovery and a sense of adventure. He comes from a family that valued travel and exploration, though his own education was somewhat neglected.", "motivations": "He seeks to achieve glory and discovery, to fulfill a lifelong dream of exploring uncharted territories, and to find a friend who understands his ambitions.", "relationships": {"Margaret Saville": "His sister, to whom he writes letters, expressing his hopes, fears, and the loneliness he feels."}, "speech_patterns": "Walton's writing style is reflective and detailed, showing a deep emotional investment in his journey and a longing for companionship.", "personality_traits": ["Determined", "Passionate", "Thoughtful", "Lonely"]}	2026-06-20 09:57:24.518186+00
4	1	13	14	\N	\N	\N	{"backstory": "Robert Walton is an explorer who has dedicated himself to a voyage to the North Pole, driven by a desire for discovery and a sense of adventure. He comes from a family that valued travel and exploration, though his own education was somewhat neglected.", "motivations": "He seeks to achieve glory and discovery, to fulfill a lifelong dream of exploring uncharted territories, and to find a friend who understands his ambitions.", "relationships": {"Margaret Saville": "His sister, to whom he writes letters, expressing his hopes, fears, and the loneliness he feels."}, "speech_patterns": "Walton's writing style is reflective and detailed, showing a deep emotional investment in his journey and a longing for companionship.", "personality_traits": ["Determined", "Passionate", "Thoughtful", "Lonely"]}	2026-06-20 09:57:24.518186+00
828	41	778	780	\N	\N	\N	{"backstory": "Scrooge is a miserly businessman who continues to operate under the name 'Scrooge and Marley', despite Marley's death.", "motivations": "Maintaining his wealth and avoiding social interactions.", "relationships": {"Marley": "Former business partner", "Nephew": "Uncle"}, "speech_patterns": "Uses short, sharp sentences, often interjecting with 'Bah!' and 'Humbug!'", "personality_note": "pragmatic", "personality_traits": ["Miserly", "Solitary", "Covetous", "Cold-hearted"]}	2026-06-20 10:54:26.207593+00
829	41	780	781	\N	\N	\N	{"backstory": "Scrooge is a miserly businessman who continues to operate under the name 'Scrooge and Marley', despite Marley's death.", "motivations": "Maintaining his wealth and avoiding social interactions.", "relationships": {"Marley": "Former business partner", "Nephew": "Uncle"}, "speech_patterns": "Uses short, sharp sentences, often interjecting with 'Bah!' and 'Humbug!'", "personality_note": "stubborn", "personality_traits": ["Miserly", "Solitary", "Covetous", "Cold-hearted"]}	2026-06-20 10:54:26.207593+00
830	41	781	782	\N	\N	\N	{"backstory": "Scrooge is a miserly businessman who continues to operate under the name 'Scrooge and Marley', despite Marley's death.", "motivations": "Maintaining his wealth and avoiding social interactions.", "relationships": {"Marley": "Former business partner", "Nephew": "Uncle"}, "speech_patterns": "Uses short, sharp sentences, often interjecting with 'Bah!' and 'Humbug!'", "personality_note": "miserly", "personality_traits": ["Miserly", "Solitary", "Covetous", "Cold-hearted"]}	2026-06-20 10:54:26.207593+00
5	1	14	15	\N	\N	\N	{"backstory": "Robert Walton is an explorer who has dedicated himself to a voyage to the North Pole, driven by a desire for discovery and a sense of adventure. He comes from a family that valued travel and exploration, though his own education was somewhat neglected.", "motivations": "He seeks to achieve glory and discovery, to fulfill a lifelong dream of exploring uncharted territories, and to find a friend who understands his ambitions.", "relationships": {"Margaret Saville": "His sister, to whom he writes letters, expressing his hopes, fears, and the loneliness he feels."}, "speech_patterns": "Walton's writing style is reflective and detailed, showing a deep emotional investment in his journey and a longing for companionship.", "personality_traits": ["Determined", "Passionate", "Thoughtful", "Lonely"]}	2026-06-20 09:57:24.518186+00
6	1	15	16	\N	\N	\N	{"backstory": "Robert Walton is an explorer who has dedicated himself to a voyage to the North Pole, driven by a desire for discovery and a sense of adventure. He comes from a family that valued travel and exploration, though his own education was somewhat neglected.", "motivations": "He seeks to achieve glory and discovery, to fulfill a lifelong dream of exploring uncharted territories, and to find a friend who understands his ambitions.", "relationships": {"Margaret Saville": "His sister, to whom he writes letters, expressing his hopes, fears, and the loneliness he feels."}, "speech_patterns": "Walton's writing style is reflective and detailed, showing a deep emotional investment in his journey and a longing for companionship.", "personality_traits": ["Determined", "Passionate", "Thoughtful", "Lonely"]}	2026-06-20 09:57:24.518186+00
831	41	782	783	\N	\N	\N	{"backstory": "Scrooge is a miserly businessman who continues to operate under the name 'Scrooge and Marley', despite Marley's death.", "motivations": "Maintaining his wealth and avoiding social interactions.", "relationships": {"Marley": "Former business partner", "Nephew": "Uncle"}, "speech_patterns": "Uses short, sharp sentences, often interjecting with 'Bah!' and 'Humbug!'", "personality_note": "unyielding", "personality_traits": ["Miserly", "Solitary", "Covetous", "Cold-hearted"]}	2026-06-20 10:54:26.207593+00
7	1	16	17	\N	\N	\N	{"backstory": "Robert Walton is an explorer who has dedicated himself to a voyage to the North Pole, driven by a desire for discovery and a sense of adventure. He comes from a family that valued travel and exploration, though his own education was somewhat neglected.", "motivations": "He seeks to achieve glory and discovery, to fulfill a lifelong dream of exploring uncharted territories, and to find a friend who understands his ambitions.", "relationships": {"Margaret Saville": "His sister, to whom he writes letters, expressing his hopes, fears, and the loneliness he feels."}, "speech_patterns": "Walton's writing style is reflective and detailed, showing a deep emotional investment in his journey and a longing for companionship.", "personality_traits": ["Determined", "Passionate", "Thoughtful", "Lonely"]}	2026-06-20 09:57:24.518186+00
8	1	17	23	\N	\N	\N	{"backstory": "Robert Walton is an explorer who has dedicated himself to a voyage to the North Pole, driven by a desire for discovery and a sense of adventure. He comes from a family that valued travel and exploration, though his own education was somewhat neglected.", "motivations": "He seeks to achieve glory and discovery, to fulfill a lifelong dream of exploring uncharted territories, and to find a friend who understands his ambitions.", "relationships": {"Margaret Saville": "His sister, to whom he writes letters, expressing his hopes, fears, and the loneliness he feels."}, "speech_patterns": "Walton's writing style is reflective and detailed, showing a deep emotional investment in his journey and a longing for companionship.", "personality_traits": ["Determined", "Passionate", "Thoughtful", "Lonely"]}	2026-06-20 09:57:24.518186+00
23	5	50	54	countenance assumes deepest gloom	\N	\N	{"backstory": "A mysterious figure found on a sledge, having endured great hardships and pursuing a relentless quest.", "motivations": "Driven by a complex mix of revenge and regret, seeking to confront a past that haunts him.", "relationships": {"Captain Walton": "Grateful for rescue, forming a close and supportive relationship."}, "speech_patterns": "Uses eloquent and carefully chosen words, often expressing deep emotions.", "personality_traits": ["Melancholic", "Passionate", "Gentle"]}	2026-06-20 09:57:24.518186+00
832	41	783	784	\N	\N	\N	{"backstory": "Scrooge is a miserly businessman who continues to operate under the name 'Scrooge and Marley', despite Marley's death.", "motivations": "Maintaining his wealth and avoiding social interactions.", "relationships": {"Marley": "Former business partner", "Nephew": "Uncle"}, "speech_patterns": "Uses short, sharp sentences, often interjecting with 'Bah!' and 'Humbug!'", "personality_note": "unyielding", "personality_traits": ["Miserly", "Solitary", "Covetous", "Cold-hearted"]}	2026-06-20 10:54:26.207593+00
9	1	23	24	\N	\N	\N	{"backstory": "Robert Walton is an explorer who has dedicated himself to a voyage to the North Pole, driven by a desire for discovery and a sense of adventure. He comes from a family that valued travel and exploration, though his own education was somewhat neglected.", "motivations": "He seeks to achieve glory and discovery, to fulfill a lifelong dream of exploring uncharted territories, and to find a friend who understands his ambitions.", "relationships": {"Margaret Saville": "His sister, to whom he writes letters, expressing his hopes, fears, and the loneliness he feels."}, "speech_patterns": "Walton's writing style is reflective and detailed, showing a deep emotional investment in his journey and a longing for companionship.", "personality_traits": ["Determined", "Passionate", "Thoughtful", "Lonely"]}	2026-06-20 09:57:24.518186+00
10	1	24	25	\N	\N	\N	{"backstory": "Robert Walton is an explorer who has dedicated himself to a voyage to the North Pole, driven by a desire for discovery and a sense of adventure. He comes from a family that valued travel and exploration, though his own education was somewhat neglected.", "motivations": "He seeks to achieve glory and discovery, to fulfill a lifelong dream of exploring uncharted territories, and to find a friend who understands his ambitions.", "relationships": {"Margaret Saville": "feels more distant due to lack of physical presence"}, "speech_patterns": "Walton's writing style is reflective and detailed, showing a deep emotional investment in his journey and a longing for companionship.", "personality_note": "increased longing for companionship", "personality_traits": ["Determined", "Passionate", "Thoughtful", "Lonely"]}	2026-06-20 09:57:24.518186+00
833	41	784	788	\N	\N	\N	{"backstory": "Scrooge is a miserly businessman who continues to operate under the name 'Scrooge and Marley', despite Marley's death.", "motivations": "Maintaining his wealth and avoiding social interactions.", "relationships": {"Marley": "Former business partner", "Nephew": "Uncle"}, "speech_patterns": "Uses short, sharp sentences, often interjecting with 'Bah!' and 'Humbug!'", "personality_note": "content_with_isolation", "personality_traits": ["Miserly", "Solitary", "Covetous", "Cold-hearted"]}	2026-06-20 10:54:26.207593+00
12	3	25	26	\N	\N	\N	{"backstory": "The lieutenant is a man of courage and enterprise, known for his kindliness and the respect he commands from his crew. He has a romantic past involving a Russian lady and a rival.", "motivations": "Driven by a desire for advancement and recognition, he also possesses a noble spirit that values others' happiness over his own.", "relationships": {"Robert Walton": "Engaged by Walton to assist in his enterprise, the lieutenant is seen as a valuable addition to the crew due to his qualities."}, "speech_patterns": "Not directly quoted, but described as silent and uncommunicative, with a depth of character that is not immediately apparent.", "personality_note": "noble and courageous despite rough exterior", "personality_traits": ["Courageous", "Kind", "Reserved", "Noble"]}	2026-06-20 09:57:24.518186+00
835	42	787	789	\N	\N	\N	{"backstory": "Scrooge's nephew, who tries to engage Scrooge in a friendly manner and invites him to dinner.", "motivations": "Wants to bring joy and warmth to Scrooge.", "relationships": {"Scrooge": "Nephew"}, "speech_patterns": "Uses positive language and defends the spirit of Christmas.", "personality_traits": ["Cheerful", "Optimistic", "Friendly"]}	2026-06-20 10:54:26.207593+00
837	42	789	790	\N	\N	\N	{"backstory": "Scrooge's nephew, who tries to engage Scrooge in a friendly manner and invites him to dinner.", "motivations": "Wants to bring joy and warmth to Scrooge.", "relationships": {"Scrooge": "Nephew"}, "speech_patterns": "Uses positive language and defends the spirit of Christmas.", "personality_traits": ["Cheerful", "Optimistic", "Friendly"]}	2026-06-20 10:54:26.207593+00
836	41	788	791	\N	\N	\N	{"backstory": "Scrooge is a miserly businessman who continues to operate under the name 'Scrooge and Marley', despite Marley's death.", "motivations": "Maintaining his wealth and avoiding social interactions.", "relationships": {"Marley": "Former business partner", "Nephew": "Uncle"}, "speech_patterns": "Uses short, sharp sentences, often interjecting with 'Bah!' and 'Humbug!'", "personality_note": "content_with_isolation", "personality_traits": ["Miserly", "Solitary", "Covetous", "Cold-hearted"]}	2026-06-20 10:54:26.207593+00
11	1	25	27	\N	\N	\N	{"backstory": "Robert Walton is an explorer who has dedicated himself to a voyage to the North Pole, driven by a desire for discovery and a sense of adventure. He comes from a family that valued travel and exploration, though his own education was somewhat neglected.", "motivations": "He seeks to achieve glory and discovery, to fulfill a lifelong dream of exploring uncharted territories, and to find a friend who understands his ambitions.", "relationships": {"Margaret Saville": "feels more distant due to lack of physical presence"}, "speech_patterns": "Walton's writing style is reflective and detailed, showing a deep emotional investment in his journey and a longing for companionship.", "personality_note": "increased longing for companionship", "personality_traits": ["Determined", "Passionate", "Thoughtful", "Lonely"]}	2026-06-20 09:57:24.518186+00
839	41	791	795	\N	\N	\N	{"backstory": "Scrooge is a miserly businessman who continues to operate under the name 'Scrooge and Marley', despite Marley's death.", "motivations": "Maintaining his wealth and avoiding social interactions.", "relationships": {"Marley": "Former business partner", "Nephew": "Uncle", "change": "hostility", "target": "Scrooge's Nephew"}, "speech_patterns": "Uses short, sharp sentences, often interjecting with 'Bah!' and 'Humbug!'", "personality_note": "increased cynicism", "personality_traits": ["Miserly", "Solitary", "Covetous", "Cold-hearted"]}	2026-06-20 10:54:26.207593+00
14	1	27	28	\N	\N	\N	{"backstory": "Robert Walton is an explorer who has dedicated himself to a voyage to the North Pole, driven by a desire for discovery and a sense of adventure. He comes from a family that valued travel and exploration, though his own education was somewhat neglected.", "motivations": "He seeks to achieve glory and discovery, to fulfill a lifelong dream of exploring uncharted territories, and to find a friend who understands his ambitions.", "relationships": {"Margaret Saville": "feels more distant due to lack of physical presence"}, "speech_patterns": "Walton's writing style is reflective and detailed, showing a deep emotional investment in his journey and a longing for companionship.", "personality_note": "increased longing for companionship", "personality_traits": ["Determined", "Passionate", "Thoughtful", "Lonely"]}	2026-06-20 09:57:24.518186+00
15	1	28	29	\N	\N	\N	{"backstory": "Robert Walton is an explorer who has dedicated himself to a voyage to the North Pole, driven by a desire for discovery and a sense of adventure. He comes from a family that valued travel and exploration, though his own education was somewhat neglected.", "motivations": "He seeks to achieve glory and discovery, to fulfill a lifelong dream of exploring uncharted territories, and to find a friend who understands his ambitions.", "relationships": {"Margaret Saville": "feels more distant due to lack of physical presence"}, "speech_patterns": "Walton's writing style is reflective and detailed, showing a deep emotional investment in his journey and a longing for companionship.", "personality_note": "passionate and enthusiastic about the mysterious and dangerous aspects of the ocean", "personality_traits": ["Determined", "Passionate", "Thoughtful", "Lonely"]}	2026-06-20 09:57:24.518186+00
840	41	795	799	\N	\N	\N	{"backstory": "Scrooge is a miserly businessman who continues to operate under the name 'Scrooge and Marley', despite Marley's death.", "motivations": "Maintaining his wealth and avoiding social interactions.", "relationships": {"Marley": "Former business partner", "Nephew": "Uncle", "change": "hostility", "target": "general_public"}, "speech_patterns": "Uses short, sharp sentences, often interjecting with 'Bah!' and 'Humbug!'", "personality_note": "increased bitterness", "personality_traits": ["Miserly", "Solitary", "Covetous", "Cold-hearted"]}	2026-06-20 10:54:26.207593+00
16	1	29	30	\N	\N	\N	{"backstory": "Robert Walton is an explorer who has dedicated himself to a voyage to the North Pole, driven by a desire for discovery and a sense of adventure. He comes from a family that valued travel and exploration, though his own education was somewhat neglected.", "motivations": "He seeks to achieve glory and discovery, to fulfill a lifelong dream of exploring uncharted territories, and to find a friend who understands his ambitions.", "relationships": {"Margaret Saville": "feels more distant due to lack of physical presence"}, "speech_patterns": "Walton's writing style is reflective and detailed, showing a deep emotional investment in his journey and a longing for companionship.", "personality_note": "passionate and enthusiastic about the mysterious and dangerous aspects of the ocean", "personality_traits": ["Determined", "Passionate", "Thoughtful", "Lonely"]}	2026-06-20 09:57:24.518186+00
838	42	790	800	\N	\N	\N	{"backstory": "Scrooge's nephew, who tries to engage Scrooge in a friendly manner and invites him to dinner.", "motivations": "Wants to bring joy and warmth to Scrooge.", "relationships": {"Scrooge": "Nephew"}, "speech_patterns": "Uses positive language and defends the spirit of Christmas.", "personality_traits": ["Cheerful", "Optimistic", "Friendly"]}	2026-06-20 10:54:26.207593+00
834	45	786	801	\N	\N	\N	{"backstory": "An unnamed clerk working for Scrooge, who is cheerful and tries to enjoy the holiday despite Scrooge's grumpiness.", "motivations": "Wants to enjoy the Christmas holiday and maintain his job despite Scrooge's demeanor.", "relationships": {"Scrooge": "Employee"}, "speech_patterns": "Often speaks in a mild manner, trying to avoid confrontation.", "personality_traits": ["cheerful", "modest", "resilient"]}	2026-06-20 10:54:26.207593+00
13	3	26	42	\N	\N	\N	{"backstory": "The lieutenant is a man of courage and enterprise, known for his kindliness and the respect he commands from his crew. He has a romantic past involving a Russian lady and a rival.", "motivations": "Driven by a desire for advancement and recognition, he also possesses a noble spirit that values others' happiness over his own.", "relationships": {"Robert Walton": "Engaged by Walton to assist in his enterprise, the lieutenant is seen as a valuable addition to the crew due to his qualities."}, "speech_patterns": "Not directly quoted, but described as silent and uncommunicative, with a depth of character that is not immediately apparent.", "personality_note": "generous and selfless, but uneducated and silent", "personality_traits": ["Courageous", "Kind", "Reserved", "Noble"]}	2026-06-20 09:57:24.518186+00
17	1	30	42	\N	\N	\N	{"backstory": "Robert Walton is an explorer who has dedicated himself to a voyage to the North Pole, driven by a desire for discovery and a sense of adventure. He comes from a family that valued travel and exploration, though his own education was somewhat neglected.", "motivations": "He seeks to achieve glory and discovery, to fulfill a lifelong dream of exploring uncharted territories, and to find a friend who understands his ambitions.", "relationships": {"Margaret Saville": "feels more distant due to lack of physical presence"}, "speech_patterns": "Walton's writing style is reflective and detailed, showing a deep emotional investment in his journey and a longing for companionship.", "personality_note": "passionate and enthusiastic about the mysterious and dangerous aspects of the ocean", "personality_traits": ["Determined", "Passionate", "Thoughtful", "Lonely"]}	2026-06-20 09:57:24.518186+00
19	3	42	\N	\N	astonished	\N	{"backstory": "The lieutenant is a man of courage and enterprise, known for his kindliness and the respect he commands from his crew. He has a romantic past involving a Russian lady and a rival.", "motivations": "Driven by a desire for advancement and recognition, he also possesses a noble spirit that values others' happiness over his own.", "relationships": {"Robert Walton": "Engaged by Walton to assist in his enterprise, the lieutenant is seen as a valuable addition to the crew due to his qualities."}, "speech_patterns": "Not directly quoted, but described as silent and uncommunicative, with a depth of character that is not immediately apparent.", "personality_note": "generous and selfless, but uneducated and silent", "personality_traits": ["Courageous", "Kind", "Reserved", "Noble"]}	2026-06-20 09:57:24.518186+00
841	41	799	802	\N	\N	\N	{"backstory": "Scrooge is a miserly businessman who continues to operate under the name 'Scrooge and Marley', despite Marley's death.", "motivations": "Maintaining his wealth and avoiding social interactions.", "relationships": {"Marley": "Former business partner", "Nephew": "Uncle", "change": "hostility", "target": "general_public"}, "speech_patterns": "Uses short, sharp sentences, often interjecting with 'Bah!' and 'Humbug!'", "personality_note": "increased bitterness", "personality_traits": ["Miserly", "Solitary", "Covetous", "Cold-hearted"]}	2026-06-20 10:54:26.207593+00
20	5	44	47	limbs nearly frozen, body emaciated	\N	\N	{"backstory": "A mysterious figure found on a sledge, having endured great hardships and pursuing a relentless quest.", "motivations": "Driven by a complex mix of revenge and regret, seeking to confront a past that haunts him.", "relationships": {"Captain Walton": "Grateful for rescue, forming a close and supportive relationship."}, "speech_patterns": "Uses eloquent and carefully chosen words, often expressing deep emotions.", "personality_traits": ["Melancholic", "Passionate", "Gentle"]}	2026-06-20 09:57:24.518186+00
842	42	800	802	\N	\N	\N	{"backstory": "Scrooge's nephew, who tries to engage Scrooge in a friendly manner and invites him to dinner.", "motivations": "Wants to bring joy and warmth to Scrooge.", "relationships": {"Scrooge": "Nephew"}, "speech_patterns": "Uses positive language and defends the spirit of Christmas.", "personality_traits": ["Cheerful", "Optimistic", "Friendly"]}	2026-06-20 10:54:26.207593+00
21	5	47	48	faints after leaving fresh air, then revived with brandy and soup	\N	\N	{"backstory": "A mysterious figure found on a sledge, having endured great hardships and pursuing a relentless quest.", "motivations": "Driven by a complex mix of revenge and regret, seeking to confront a past that haunts him.", "relationships": {"Captain Walton": "Grateful for rescue, forming a close and supportive relationship."}, "speech_patterns": "Uses eloquent and carefully chosen words, often expressing deep emotions.", "personality_traits": ["Melancholic", "Passionate", "Gentle"]}	2026-06-20 09:57:24.518186+00
22	5	48	50	eyes show wildness and madness, but also benevolence and sweetness	\N	\N	{"backstory": "A mysterious figure found on a sledge, having endured great hardships and pursuing a relentless quest.", "motivations": "Driven by a complex mix of revenge and regret, seeking to confront a past that haunts him.", "relationships": {"Captain Walton": "Grateful for rescue, forming a close and supportive relationship."}, "speech_patterns": "Uses eloquent and carefully chosen words, often expressing deep emotions.", "personality_traits": ["Melancholic", "Passionate", "Gentle"]}	2026-06-20 09:57:24.518186+00
845	42	802	803	\N	\N	\N	{"backstory": "Scrooge's nephew, who tries to engage Scrooge in a friendly manner and invites him to dinner.", "motivations": "Wants to bring joy and warmth to Scrooge.", "relationships": {"Scrooge": "Nephew"}, "speech_patterns": "Uses positive language and defends the spirit of Christmas.", "personality_traits": ["Cheerful", "Optimistic", "Friendly"]}	2026-06-20 10:54:26.207593+00
24	5	54	58	countenance assumes deepest gloom	\N	\N	{"backstory": "A mysterious figure found on a sledge, having endured great hardships and pursuing a relentless quest.", "motivations": "Driven by a complex mix of revenge and regret, seeking to confront a past that haunts him.", "relationships": {"Captain Walton": "Grateful for rescue, forming a close and supportive relationship."}, "speech_patterns": "Uses eloquent and carefully chosen words, often expressing deep emotions.", "personality_traits": ["Melancholic", "Passionate", "Gentle"]}	2026-06-20 09:57:24.518186+00
844	41	802	804	\N	\N	\N	{"backstory": "Scrooge is a miserly businessman who continues to operate under the name 'Scrooge and Marley', despite Marley's death.", "motivations": "Maintaining his wealth and avoiding social interactions.", "relationships": {"Marley": "Former business partner", "Nephew": "Uncle", "change": "hostility", "target": "general_public"}, "speech_patterns": "Uses short, sharp sentences, often interjecting with 'Bah!' and 'Humbug!'", "personality_note": "increased bitterness", "personality_traits": ["Miserly", "Solitary", "Covetous", "Cold-hearted"]}	2026-06-20 10:54:26.207593+00
18	1	42	60	\N	astonished	\N	{"backstory": "Robert Walton is an explorer who has dedicated himself to a voyage to the North Pole, driven by a desire for discovery and a sense of adventure. He comes from a family that valued travel and exploration, though his own education was somewhat neglected.", "motivations": "He seeks to achieve glory and discovery, to fulfill a lifelong dream of exploring uncharted territories, and to find a friend who understands his ambitions.", "relationships": {"Margaret Saville": "feels more distant due to lack of physical presence"}, "speech_patterns": "Walton's writing style is reflective and detailed, showing a deep emotional investment in his journey and a longing for companionship.", "personality_note": "passionate and enthusiastic about the mysterious and dangerous aspects of the ocean", "personality_traits": ["Determined", "Passionate", "Thoughtful", "Lonely"]}	2026-06-20 09:57:24.518186+00
27	1	60	63	\N	astonished	\N	{"backstory": "Robert Walton is an explorer who has dedicated himself to a voyage to the North Pole, driven by a desire for discovery and a sense of adventure. He comes from a family that valued travel and exploration, though his own education was somewhat neglected.", "motivations": "He seeks to achieve glory and discovery, to fulfill a lifelong dream of exploring uncharted territories, and to find a friend who understands his ambitions.", "relationships": {"The Stranger": "growing affection", "Margaret Saville": "feels more distant due to lack of physical presence"}, "speech_patterns": "Walton's writing style is reflective and detailed, showing a deep emotional investment in his journey and a longing for companionship.", "personality_note": "increased empathy", "personality_traits": ["Determined", "Passionate", "Thoughtful", "Lonely"]}	2026-06-20 09:57:24.518186+00
846	42	803	805	\N	\N	\N	{"backstory": "Scrooge's nephew, who tries to engage Scrooge in a friendly manner and invites him to dinner.", "motivations": "Wants to bring joy and warmth to Scrooge.", "relationships": {"Scrooge": "Nephew"}, "speech_patterns": "Uses positive language and defends the spirit of Christmas.", "personality_traits": ["Cheerful", "Optimistic", "Friendly"]}	2026-06-20 10:54:26.207593+00
25	5	58	64	countenance assumes deepest gloom	\N	\N	{"backstory": "A mysterious figure found on a sledge, having endured great hardships and pursuing a relentless quest.", "motivations": "Driven by a complex mix of revenge and regret, seeking to confront a past that haunts him.", "relationships": {"Captain Walton": "Grateful for rescue, forming a close and supportive relationship."}, "speech_patterns": "Uses eloquent and carefully chosen words, often expressing deep emotions.", "personality_traits": ["Melancholic", "Passionate", "Gentle"]}	2026-06-20 09:57:24.518186+00
29	5	64	65	countenance assumes deepest gloom	\N	\N	{"backstory": "A mysterious figure found on a sledge, having endured great hardships and pursuing a relentless quest.", "motivations": "Driven by a complex mix of revenge and regret, seeking to confront a past that haunts him.", "relationships": {"Robert Walton": "shared madness", "Captain Walton": "Grateful for rescue, forming a close and supportive relationship."}, "speech_patterns": "Uses eloquent and carefully chosen words, often expressing deep emotions.", "personality_note": "reveals inner turmoil", "personality_traits": ["Melancholic", "Passionate", "Gentle"]}	2026-06-20 09:57:24.518186+00
847	41	804	806	\N	\N	\N	{"backstory": "Scrooge is a miserly businessman who continues to operate under the name 'Scrooge and Marley', despite Marley's death.", "motivations": "Maintaining his wealth and avoiding social interactions.", "relationships": {"Marley": "Former business partner", "Nephew": "Uncle", "change": "hostility", "target": "general_public"}, "speech_patterns": "Uses short, sharp sentences, often interjecting with 'Bah!' and 'Humbug!'", "personality_note": "increased bitterness", "personality_traits": ["Miserly", "Solitary", "Covetous", "Cold-hearted"]}	2026-06-20 10:54:26.207593+00
28	1	63	66	\N	astonished	\N	{"backstory": "Robert Walton is an explorer who has dedicated himself to a voyage to the North Pole, driven by a desire for discovery and a sense of adventure. He comes from a family that valued travel and exploration, though his own education was somewhat neglected.", "motivations": "He seeks to achieve glory and discovery, to fulfill a lifelong dream of exploring uncharted territories, and to find a friend who understands his ambitions.", "relationships": {"The Stranger": "deepening admiration and pity", "Margaret Saville": "feels more distant due to lack of physical presence"}, "speech_patterns": "Walton's writing style is reflective and detailed, showing a deep emotional investment in his journey and a longing for companionship.", "personality_note": "increased empathy", "personality_traits": ["Determined", "Passionate", "Thoughtful", "Lonely"]}	2026-06-20 09:57:24.518186+00
849	41	806	808	\N	\N	\N	{"backstory": "Scrooge is a miserly businessman who continues to operate under the name 'Scrooge and Marley', despite Marley's death.", "motivations": "Maintaining his wealth and avoiding social interactions.", "relationships": {"Marley": "Former business partner", "Nephew": "Uncle", "change": "hostility", "target": "general_public"}, "speech_patterns": "Uses short, sharp sentences, often interjecting with 'Bah!' and 'Humbug!'", "personality_note": "increased bitterness", "personality_traits": ["Miserly", "Solitary", "Covetous", "Cold-hearted"]}	2026-06-20 10:54:26.207593+00
30	5	65	67	countenance assumes deepest gloom	\N	\N	{"backstory": "A mysterious figure found on a sledge, having endured great hardships and pursuing a relentless quest.", "motivations": "Driven by a complex mix of revenge and regret, seeking to confront a past that haunts him.", "relationships": {"Robert Walton": "shared understanding", "Captain Walton": "Grateful for rescue, forming a close and supportive relationship."}, "speech_patterns": "Uses eloquent and carefully chosen words, often expressing deep emotions.", "personality_note": "overwhelmed by emotion", "personality_traits": ["Melancholic", "Passionate", "Gentle"]}	2026-06-20 09:57:24.518186+00
32	5	67	68	countenance assumes deepest gloom	\N	\N	{"backstory": "A mysterious figure found on a sledge, having endured great hardships and pursuing a relentless quest.", "motivations": "Driven by a complex mix of revenge and regret, seeking to confront a past that haunts him.", "relationships": {"Robert Walton": "shared understanding", "Captain Walton": "Grateful for rescue, forming a close and supportive relationship."}, "speech_patterns": "Uses eloquent and carefully chosen words, often expressing deep emotions.", "personality_note": "reveals sense of loss", "personality_traits": ["Melancholic", "Passionate", "Gentle"]}	2026-06-20 09:57:24.518186+00
851	41	808	816	\N	\N	\N	{"backstory": "Scrooge is a miserly businessman who continues to operate under the name 'Scrooge and Marley', despite Marley's death.", "motivations": "Maintaining his wealth and avoiding social interactions.", "relationships": {"Marley": "Former business partner", "Nephew": "Uncle", "change": "dismisses her feelings and reasons", "target": "general_public", "with_who": "Belle"}, "speech_patterns": "Uses short, sharp sentences, often interjecting with 'Bah!' and 'Humbug!'", "personality_note": "increased bitterness", "personality_traits": ["Miserly", "Solitary", "Covetous", "Cold-hearted"]}	2026-06-20 10:54:26.207593+00
33	5	68	69	countenance assumes deepest gloom	\N	\N	{"backstory": "A mysterious figure found on a sledge, having endured great hardships and pursuing a relentless quest.", "motivations": "Driven by a complex mix of revenge and regret, seeking to confront a past that haunts him.", "relationships": {"Robert Walton": "shared understanding", "Captain Walton": "Grateful for rescue, forming a close and supportive relationship."}, "speech_patterns": "Uses eloquent and carefully chosen words, often expressing deep emotions.", "personality_note": "reveals sense of loss", "personality_traits": ["Melancholic", "Passionate", "Gentle"]}	2026-06-20 09:57:24.518186+00
34	5	69	70	countenance assumes deepest gloom	\N	\N	{"backstory": "A mysterious figure found on a sledge, having endured great hardships and pursuing a relentless quest.", "motivations": "Driven by a complex mix of revenge and regret, seeking to confront a past that haunts him.", "relationships": {"Robert Walton": "shared understanding", "Captain Walton": "Grateful for rescue, forming a close and supportive relationship."}, "speech_patterns": "Uses eloquent and carefully chosen words, often expressing deep emotions.", "personality_note": "reveals sense of loss", "personality_traits": ["Melancholic", "Passionate", "Gentle"]}	2026-06-20 09:57:24.518186+00
843	45	801	817	\N	\N	\N	{"backstory": "An unnamed clerk working for Scrooge, who is cheerful and tries to enjoy the holiday despite Scrooge's grumpiness.", "motivations": "Wants to enjoy the Christmas holiday and maintain his job despite Scrooge's demeanor.", "relationships": {"Scrooge": "Employee"}, "speech_patterns": "Often speaks in a mild manner, trying to avoid confrontation.", "personality_traits": ["cheerful", "modest", "resilient"]}	2026-06-20 10:54:26.207593+00
35	5	70	71	countenance assumes deepest gloom	\N	\N	{"backstory": "A mysterious figure found on a sledge, having endured great hardships and pursuing a relentless quest.", "motivations": "Driven by a complex mix of revenge and regret, seeking to confront a past that haunts him.", "relationships": {"Robert Walton": "shared understanding", "Captain Walton": "Grateful for rescue, forming a close and supportive relationship."}, "speech_patterns": "Uses eloquent and carefully chosen words, often expressing deep emotions.", "personality_note": "reveals sense of loss", "personality_traits": ["Melancholic", "Passionate", "Gentle"]}	2026-06-20 09:57:24.518186+00
852	41	816	818	\N	\N	\N	{"backstory": "Scrooge is a miserly businessman who continues to operate under the name 'Scrooge and Marley', despite Marley's death.", "motivations": "Maintaining his wealth and avoiding social interactions.", "relationships": {"Marley": "Former business partner", "Nephew": "Uncle", "change": "dismisses her feelings and reasons", "target": "general_public", "with_who": "Belle"}, "speech_patterns": "Uses short, sharp sentences, often interjecting with 'Bah!' and 'Humbug!'", "personality_note": "increased bitterness", "personality_traits": ["Miserly", "Solitary", "Covetous", "Cold-hearted"]}	2026-06-20 10:54:26.207593+00
26	38	59	72	\N	\N	\N	{"backstory": "Captain of a ship that rescues Victor, listening to his tale of woe and vengeful pursuit.", "motivations": "Driven by a desire to understand Victor's plight and possibly assist in his quest.", "relationships": {"The Stranger": "Walton begins to love the stranger as a brother.", "Victor Frankenstein": "Listener and potential ally in Victor's pursuit."}, "speech_patterns": "Walton listens intently, occasionally interjecting with questions or expressions of sympathy.", "personality_traits": ["Curious", "Empathetic", "Adventurous"]}	2026-06-20 09:57:24.518186+00
36	5	71	73	countenance assumes deepest gloom	\N	\N	{"backstory": "A mysterious figure found on a sledge, having endured great hardships and pursuing a relentless quest.", "motivations": "Driven by a complex mix of revenge and regret, seeking to confront a past that haunts him.", "relationships": {"Robert Walton": "shared understanding", "Captain Walton": "Grateful for rescue, forming a close and supportive relationship."}, "speech_patterns": "Uses eloquent and carefully chosen words, often expressing deep emotions.", "personality_note": "reveals sense of loss", "personality_traits": ["Melancholic", "Passionate", "Gentle"]}	2026-06-20 09:57:24.518186+00
38	5	73	74	countena... (3 MB left)