const express = require("express")
const app = express()

var cors = require("cors")
app.use(cors())

const mongoose = require("mongoose")

const bodyParser = require("body-parser")
app.use(bodyParser.urlencoded({ extended: false }))
app.use(bodyParser.json())

connect().catch(err => console.log(err))

async function connect() {
  await mongoose.connect(process.env.MONGODB_URI)
  console.log("Successfully connected to MongoDB")
}

const musicSchema = new mongoose.Schema({
    title: String,
    artist: String,
    genre: String,
    releaseyear: String
  });

const Song = mongoose.model('Songs', musicSchema);

app.post("/new", async (req, res) => {
    const newSong = new Song({
        title: req.body.title,
        artist: req.body.artist,
        genre: req.body.genre,
        releaseyear: req.body.releaseyear
    })
    await newSong.save()
    res.json(newSong)
})

app.get("/songs", async (req, res) => {
    const songs = await Song.find()
    res.send(songs)
})

app.listen(3000, () => {
    console.log("Listening on port 3000")
})